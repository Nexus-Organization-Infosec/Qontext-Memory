#!/usr/bin/env python3
"""Cheap hybrid rerank: cosine top-30, reordered by lexical overlap, cut to K.

The problem this targets, stated in RP_FINDINGS.md ("RETRACTION: script and
consequence are unreachable by similarity"): at K=30/budget 1500 the held-out
score is 68%, but the pack triples and the control drops from 19.2x to 5.4x.
Rank data showed the correct knot for the hard categories sits at median rank
~24 of 172 under pure cosine -- reachable, just outside a 6-slot window.

The bet here: reach (K=30) already proves the knot is IN the candidate pool.
What's missing is precision -- getting it from rank ~24 to rank ~5 WITHIN
that pool, so a cheap K=6 proposal list can still catch it and the pack stays
small. This adds one cheap signal (stemmed word overlap between knot and
query) on top of cosine, only to reorder the top-30 cosine candidates -- it
never changes what's reachable, only what's cheap to reach.

The obvious risk, stated before running anything: the hard categories (script,
consequence) were deliberately WRITTEN to share almost no vocabulary with
their query. Their lexical overlap is 0 by construction. A hybrid score can
only help them if 0 overlap still beats whatever overlap a distractor in the
pool has -- otherwise this makes their rank WORSE, not better. That has to be
measured, not assumed.

    python hybrid_rerank_bench.py --rankshift          # per-item rank diagnostic, suite A
    python hybrid_rerank_bench.py --score              # real+control, both suites
"""

import argparse
import statistics
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LIVE = HERE.parent / "qontext-live"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LIVE))

import qontext_memory as qm                                    # noqa: E402
import turn_bench as tb                                        # noqa: E402
import bridge_bench as bb                                      # noqa: E402

POOL = 30           # cosine candidates considered for reranking -- proven reach
MODEL = ("minishlab/potion-base-8M", "static")


def _jaccard(a, b):
    if not a and not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _target_indices(knots, keywords):
    """Which stored knot(s) contain the written answer key."""
    low = [k.lower() for k in knots]
    return [i for i, k in enumerate(low) if any(kw in k for kw in keywords)]


# ------------------------------------------------------------- rank shift

def rank_shift(pairs, lam, seed=7, turns=800, pool=POOL):
    """For each turn-shaped pair: cosine-only rank vs hybrid-reranked rank of
    the correct knot, restricted to the top-`pool` cosine candidates (items
    outside the pool are untouched by construction -- this only reorders)."""
    embed = bb._embedder(*MODEL)
    conv = tb.build(turns, seed, 0.10, "daily-clean", pairs)
    mem = tb.memory(conv)
    knots = mem.entries()
    kv = embed(knots)

    rows = []
    for i, (_st, kw, query, kind) in enumerate(pairs):
        if kind == "quiz":
            continue
        targets = _target_indices(knots, kw)
        if not targets:
            rows.append((kind, kw[0], None, None, "EXTRACTION MISS"))
            continue
        qv = embed([query])[0]
        sims = kv @ qv
        cos_order = list(np.argsort(-sims))
        cos_rank = min(cos_order.index(t) + 1 for t in targets)

        pool_idx = cos_order[:pool]
        qwords = set(qm._words(query))
        hybrid_scores = []
        for idx in pool_idx:
            lex = _jaccard(qwords, set(qm._words(knots[idx])))
            hybrid_scores.append((float(sims[idx]) + lam * lex, idx))
        hybrid_scores.sort(key=lambda p: -p[0])
        hybrid_order = [idx for _s, idx in hybrid_scores]
        if any(t in hybrid_order for t in targets):
            hyb_rank = min(hybrid_order.index(t) + 1 for t in targets
                            if t in hybrid_order)
        else:
            hyb_rank = cos_rank  # not in pool; rerank cannot touch it

        rows.append((kind, kw[0], cos_rank, hyb_rank, None))
    return rows


def print_rank_shift(pairs, suite_name, lam):
    rows = rank_shift(pairs, lam)
    print("\nsuite %s, lambda=%.2f, pool=%d -- per-item rank (1=best, of full store)"
          % (suite_name, lam, POOL))
    print("%-14s %-14s %6s %6s %6s" % ("kind", "key", "cosine", "hybrid", "delta"))
    deltas = []
    for kind, key, cos_r, hyb_r, note in rows:
        if note:
            print("%-14s %-14s   %s" % (kind, key, note))
            continue
        d = cos_r - hyb_r
        deltas.append(d)
        arrow = "better" if d > 0 else ("worse" if d < 0 else "same")
        print("%-14s %-14s %6d %6d %+6d  %s" % (kind, key, cos_r, hyb_r, d, arrow))
    if deltas:
        wins = sum(1 for d in deltas if d > 0)
        losses = sum(1 for d in deltas if d < 0)
        print("wins %d, losses %d, unchanged %d, mean delta %+.1f"
              % (wins, losses, len(deltas) - wins - losses, statistics.mean(deltas)))


# ------------------------------------------------------------- full score

def make_hybrid_bridge(lam, pool=POOL):
    def make(_mem):
        embed = bb._embedder(*MODEL)

        def propose(query, knots, topk):
            qv = embed([query])[0]
            kv = embed(list(knots))
            sims = kv @ qv
            order = np.argsort(-sims)[:pool]
            qwords = set(qm._words(query))
            scored = []
            for idx in order:
                lex = _jaccard(qwords, set(qm._words(knots[idx])))
                scored.append((float(sims[idx]) + lam * lex, idx))
            scored.sort(key=lambda p: -p[0])
            return [knots[i] for _s, i in scored[:topk]]
        return propose
    return make


def run_score(lam, budget, topk, seeds=(7, 23, 99), ctrl_seeds=range(1, 9)):
    make = make_hybrid_bridge(lam)
    print("\nhybrid rerank: lambda=%.2f, pool=%d, topk=%d, budget=%d"
          % (lam, POOL, topk, budget))
    for sname, spairs in tb.SUITES:
        hits_total, all_total, seps = 0, 0, []
        for cseed in seeds:
            conv = tb.build(800, cseed, 0.10, "daily-clean", spairs)
            mem = tb.memory(conv)
            propose = make(mem)
            pack = bb.packer(mem, propose, topk)
            real, by_kind = bb.score(pack, budget, lambda i: spairs[i][1],
                                     spairs, mem)
            shuf = bb.control(pack, budget, list(ctrl_seeds), spairs, mem)
            sep = real / shuf if shuf else float("inf")
            seps.append(sep)
            turn_hits = sum(v[0] for k, v in by_kind.items() if k != "quiz")
            turn_all = sum(v[1] for k, v in by_kind.items() if k != "quiz")
            hits_total += turn_hits
            all_total += turn_all
        worst = min(seps)
        flag = "" if worst >= bb.MIN_SEPARATION else "  <-- BELOW BAR"
        print("  %-14s %2d/%-2d (%2.0f%%)  worst control %.1fx%s"
              % (sname, hits_total, all_total,
                 100.0 * hits_total / all_total, worst, flag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rankshift", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--budget", type=int, default=800)
    ap.add_argument("--topk", type=int, default=6)
    args = ap.parse_args()

    if not args.rankshift and not args.score:
        args.rankshift = args.score = True

    if args.rankshift:
        for lam in (0.0, 0.25, 0.5, 1.0, 2.0):
            print_rank_shift(tb.PAIRS, "A", lam)

    if args.score:
        run_score(args.lam, args.budget, args.topk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
