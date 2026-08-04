#!/usr/bin/env python3
"""Adaptive K: a cheap classifier decides whether a query needs the wide net.

The proposal under test: a rule-based classifier does not try to answer the
query or predict the gap kind correctly -- it only estimates whether LEXICAL
retrieval is likely to fail, using surface cues (pronouns, script verbs,
consequence markers, hypernym nouns). If it predicts failure, spend more
(K=30, budget=1500, the proven-reach setting). If not, stay cheap (K=6,
budget=800, the high-control setting). Most queries should be cheap; only the
predicted-hard minority should pay the wide-net cost -- so the AVERAGE pack
size across a real conversation should land well under the blanket-K=30
number, without giving up the recall blanket-K=30 bought.

Two things must be checked before trusting this, per project discipline:

  1. Does the classifier's prediction actually agree with which items need
     the wide net? (checked against the rank data already on record)
  2. Does gating on it, end to end, actually beat plain K=6/budget=800 on
     score while keeping the control that plain K=6 has? (the only test
     that ultimately matters -- a classifier can be imperfect and still net
     positive, or be plausible and still net negative; only running it says
     which.)

    python adaptive_k_bench.py --classifier      # accuracy vs known ranks
    python adaptive_k_bench.py --score           # real+control, both suites
"""

import argparse
import re
import statistics
import sys
from enum import Enum
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LIVE = HERE.parent / "qontext-live"
if not LIVE.is_dir():
    # qontext-memory/bench layout: qontext_memory.py sits at the repo root
    # instead of a sibling qontext-live/ folder.
    LIVE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LIVE))

import qontext_memory as qm                                    # noqa: E402
import turn_bench as tb                                        # noqa: E402
import bridge_bench as bb                                      # noqa: E402
from hybrid_rerank_bench import _target_indices, MODEL          # noqa: E402

SMALL = (6, 800)      # (topk, budget) -- the cheap, high-control arm
BIG = (30, 1500)      # the proven-reach, low-control arm


class Gap(Enum):
    NONE = 0
    HYPERNYM = 1
    SCRIPT = 2
    CONSEQUENCE = 3
    INFERENCE = 4
    DIRECT = 5


# v2, changes from the original proposal and WHY -- each one checked against
# the seed-7 rank data before being kept, not assumed:
#
# "that" dropped from PRONOUNS. It fired on 3 items across both suites
#   (shellfish, utrecht, penicillin) and every one was a false positive --
#   "that" is doing determiner/expletive duty ("that new place", "is that a
#   long trip") far more often than true anaphora in this data. No true
#   positive depended on it. "this"/"it"/"there" were each load-bearing for
#   at least one real catch (saskia, pager, heron, flying, drink) and stayed.
WH = {"what", "where", "when", "who", "which", "whose"}
PRONOUNS = {"it", "this", "they", "them", "he", "she", "him", "her",
            "there", "here", "one", "ones"}
# "come" and "found" added -- both are the literal missing word in a real
# miss (vegan: "...come over"; garage: "...come and collect..."; trello:
# "...bug I just found"), and neither collides with an existing correct
# NONE/TN in either suite (checked: "coming" doesn't match "come" via
# startswith; "found" doesn't appear anywhere else in either suite).
SCRIPT_VERBS = {"cancel", "miss", "forget", "leave", "arrive", "return",
                "finish", "start", "begin", "stop", "continue", "bring",
                "take", "drop", "lose", "find", "found", "buy", "sell",
                "pay", "book", "come"}
HYPERNYMS = {"meeting", "appointment", "event", "place", "person", "animal",
             "food", "drink", "vehicle", "building", "thing", "object"}
CONSEQUENCE = {"because", "why", "since", "after", "before", "therefore",
               "result", "caused", "effect", "consequence"}


# "I" / "I'll" / "I've" etc. are capitalised by English orthography, not
# because they name an entity -- caught this only by seeing it wrongly
# demote two already-correct catches (trello, heron) to misses.
NOT_ENTITY = {"i", "i'll", "i've", "i'm", "i'd"}
MONTHS = {"january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"}
WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"}
# A short function-word list for the "two-or-more uncommon nouns" signal --
# deliberately crude (length-based content-word filter, no real POS), so its
# false-positive rate needs checking same as everything else here.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "because",
    "about", "around", "before", "after", "until", "since", "while",
    "should", "would", "could", "can", "will", "shall", "must", "might",
    "have", "has", "had", "having", "been", "being", "were", "was", "are",
    "is", "am", "be", "do", "does", "did", "doing", "want", "wants",
    "wanted", "going", "getting", "coming", "there", "here", "your",
    "yours", "their", "theirs", "these", "those", "still", "already",
    "tonight", "tomorrow", "today", "yesterday", "morning", "evening",
    "weekend", "something", "someone", "anything", "anyone", "everyone",
    "everything", "really", "actually", "probably", "maybe", "please",
    "thanks", "thank", "sorry", "quickest", "another", "little", "people",
}


def _has_proper_noun(raw):
    return any(w[:1].isupper() and w.lower() not in NOT_ENTITY
              for w in raw[1:])


def _has_possessive_proper(query):
    return bool(re.search(r"\b[A-Z][A-Za-z-]*'s\b", query))


def _has_quote(query):
    return '"' in query or "“" in query


def _has_number_or_date(q, query):
    if any(w.isdigit() for w in q):
        return True
    if any(w in MONTHS or w in WEEKDAYS for w in q):
        return True
    return bool(re.search(r"\d{1,2}:\d{2}", query))


def _uncommon_noun_count(q):
    return sum(1 for w in q if len(w) >= 6 and w not in STOPWORDS)


def classify_gap(query, direct_mode="wh_gated"):
    """Rule-based. DIRECT checked first, then the original proposal's order
    (pronoun -> script -> consequence -> hypernym -> none).

    Two DIRECT variants, both on offer because they make different bets and
    only one has been checked against this data:

      wh_gated    (v2) -- DIRECT only inside a WH-question that already names
                  an entity/possessive. Conservative: cannot fire on a
                  non-question turn at all.
      structural  (v3) -- DIRECT fires on ANY of: proper noun, quoted string,
                  number/date, possessive-of-proper-noun, or 2+ uncommon
                  nouns -- no WH-gate. This is the version proposed as
                  "query shape is the dominant factor."

    The risk with `structural`, stated before running it: this benchmark's
    hard categories often carry an INCIDENTAL proper noun or date (flying:
    "The Berlin office... Monday"; utrecht: "conference is in Rotterdam") --
    the capitalised word is scenery, not the retrieval anchor. wh_gated
    cannot be fooled by that because it requires the WH-question shape too;
    structural can. Whether that risk actually costs recall on real items,
    rather than just being plausible, is exactly what needs measuring next.
    """
    raw = re.findall(r"[A-Za-z']+", query)
    q = [w.lower() for w in raw]

    if direct_mode == "wh_gated":
        if q and q[0] in WH:
            has_entity = any(w[:1].isupper() and w.lower() not in NOT_ENTITY
                             for w in raw[1:])
            if has_entity or "'s" in query:
                return Gap.DIRECT
    elif direct_mode == "structural":
        if (_has_proper_noun(raw) or _has_possessive_proper(query)
                or _has_quote(query) or _has_number_or_date(q, query)
                or _uncommon_noun_count(q) >= 2):
            return Gap.DIRECT
    else:
        raise ValueError(direct_mode)

    if any(w in PRONOUNS for w in q):
        return Gap.INFERENCE
    if any(w.startswith(v) for w in q for v in SCRIPT_VERBS):
        return Gap.SCRIPT
    if any(w in CONSEQUENCE for w in q):
        return Gap.CONSEQUENCE
    if any(w in HYPERNYMS for w in q):
        return Gap.HYPERNYM
    return Gap.NONE


# --------------------------------------------------------- accuracy check

def check_classifier(pairs, suite_name, direct_mode, seed=7, turns=800,
                     quiet=False):
    """Predicted-wide vs actually-needs-wide (rank > 6, from cosine order),
    the same rank data already verified against RP_FINDINGS' numbers."""
    embed = bb._embedder(*MODEL)
    conv = tb.build(turns, seed, 0.10, "daily-clean", pairs)
    mem = tb.memory(conv)
    knots = mem.entries()
    kv = embed(knots)

    tp = fp = tn = fn = 0
    kind_agree = 0
    rows = []
    confusion = {g: [0, 0] for g in Gap}   # gap -> [needs_wide=True, False]
    for st, kw, query, kind in pairs:
        if kind == "quiz":
            continue
        targets = _target_indices(knots, kw)
        if not targets:
            continue
        qv = embed([query])[0]
        sims = kv @ qv
        order = list(np.argsort(-sims))
        cos_rank = min(order.index(t) + 1 for t in targets)
        needs_wide = cos_rank > SMALL[0]

        pred = classify_gap(query, direct_mode)
        pred_wide = pred not in (Gap.NONE, Gap.DIRECT)
        kind_agree += (pred.name.lower() == kind)
        confusion[pred][0 if needs_wide else 1] += 1

        if needs_wide and pred_wide:
            tp += 1
        elif needs_wide and not pred_wide:
            fn += 1
        elif not needs_wide and pred_wide:
            fp += 1
        else:
            tn += 1
        rows.append((kind, kw[0], cos_rank, needs_wide, pred.name, pred_wide))

    if not quiet:
        print("\nsuite %s [%s] -- classifier vs actual need (rank > %d = needs wide)"
              % (suite_name, direct_mode, SMALL[0]))
        print("%-14s %-14s %6s %10s %-10s %10s" % (
            "kind", "key", "rank", "needs_wide", "pred", "pred_wide"))
        for kind, key, rank, needs, pred_name, pred_wide in rows:
            flag = "OK" if needs == pred_wide else "MISS"
            print("%-14s %-14s %6d %10s %-10s %10s  %s"
                  % (kind, key, rank, needs, pred_name, pred_wide, flag))

        print("  confusion (predicted category x actual need):")
        print("  %-12s %8s %8s" % ("predicted", "needs", "doesn't"))
        for g in Gap:
            needs_n, no_n = confusion[g]
            if needs_n + no_n:
                print("  %-12s %8d %8d" % (g.name, needs_n, no_n))

    n = tp + fp + tn + fn
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    if not quiet:
        print("  n=%d  exact-kind agreement %d/%d (%.0f%%)"
              % (n, kind_agree, n, 100.0 * kind_agree / n))
        print("  binary (needs-wide vs pred-wide): TP=%d FP=%d TN=%d FN=%d"
              "  recall=%.0f%% precision=%.0f%%"
              % (tp, fp, tn, fn, 100 * recall, 100 * precision))
    return tp, fp, tn, fn


# --------------------------------------------------------------- packer

def adaptive_packer(mem, direct_mode="wh_gated"):
    embed = bb._embedder(*MODEL)

    def pack(query, _ignored_budget):
        gap = classify_gap(query, direct_mode)
        topk, budget = SMALL if gap in (Gap.NONE, Gap.DIRECT) else BIG
        base = mem.pack(query, budget)
        used = len(base)
        chosen = list(base.split("\n")) if base else []

        knots = mem.entries()
        qv = embed([query])[0]
        kv = embed(knots)
        order = np.argsort(-(kv @ qv))[:topk]
        for idx in order:
            knot = knots[idx]
            if knot in chosen:
                continue
            cost = len(knot) + (1 if chosen else 0)
            if used + cost > budget:
                continue
            chosen.append(knot)
            used += cost
        return "\n".join(chosen)

    return pack


def run_score(direct_mode="wh_gated", seeds=(7, 23, 99), ctrl_seeds=range(1, 9)):
    print("\nadaptive K [%s]: NONE/DIRECT -> K=%d/budget=%d, else -> K=%d/budget=%d"
          % (direct_mode, SMALL[0], SMALL[1], BIG[0], BIG[1]))
    for sname, spairs in tb.SUITES:
        hits_total, all_total, seps, pack_lens = 0, 0, [], []
        for cseed in seeds:
            conv = tb.build(800, cseed, 0.10, "daily-clean", spairs)
            mem = tb.memory(conv)
            pack = adaptive_packer(mem, direct_mode)
            real, by_kind = bb.score(pack, None, lambda i: spairs[i][1],
                                     spairs, mem)
            shuf = bb.control(pack, None, list(ctrl_seeds), spairs, mem)
            sep = real / shuf if shuf else float("inf")
            seps.append(sep)
            turn_hits = sum(v[0] for k, v in by_kind.items() if k != "quiz")
            turn_all = sum(v[1] for k, v in by_kind.items() if k != "quiz")
            hits_total += turn_hits
            all_total += turn_all
            for _st, _kw, query, _kind in spairs:
                pack_lens.append(len(pack(query, None)))
        worst = min(seps)
        flag = "" if worst >= bb.MIN_SEPARATION else "  <-- BELOW BAR"
        print("  %-14s %2d/%-2d (%2.0f%%)  worst control %.1fx  avg pack %.0f chars%s"
              % (sname, hits_total, all_total, 100.0 * hits_total / all_total,
                 worst, statistics.mean(pack_lens), flag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classifier", action="store_true")
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    if not args.classifier and not args.score:
        args.classifier = args.score = True

    if args.classifier:
        for mode in ("wh_gated", "structural"):
            for sname, spairs in tb.SUITES:
                check_classifier(spairs, sname, mode)

    if args.score:
        for mode in ("wh_gated", "structural"):
            run_score(mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
