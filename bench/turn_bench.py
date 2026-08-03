#!/usr/bin/env python3
"""Turn-shaped retrieval with a written answer key.

The previous turn benchmark (rp_turnbench.py) inferred ground truth from each
conversation's own continuation, and was retracted after a shuffle control
showed a reply from an *unrelated conversation* marked 97% as many facts
"needed" as the true one. See RP_FINDINGS.md.

This replaces it, keeping the property that made the retracted benchmark worth
building and discarding the one that killed it:

  KEPT      queries are conversational turns, not quiz questions. "Fancy that
            new seafood place tonight?" is what an agent actually receives.
  DISCARDED inferred ground truth. Every dependency here is *written down* --
            the fact, the turn that needs it, and the words that prove it.

The construction: plant a fact early, then write a later turn that a correct
continuation cannot be produced without, and record which fact it needs. The
turn is written to share as little vocabulary with the fact as possible, since
lexical overlap is exactly what makes quiz questions unrepresentative.

Each pair is labelled with the KIND of gap the retriever must cross, so the
result is a profile rather than one number:

  quiz         the question names its target ("What is the user's job?").
               Not turn-shaped. Present as a sanity anchor -- if this is not
               near-perfect, something is broken upstream of the experiment.
  hypernym     the turn names a category containing the fact's term.
               shellfish -> seafood.
  reference    the turn refers to the fact's subject without naming it.
               "your brother" -> Joris.
  script       the turn invokes a situation the fact bears on.
               night shifts -> "I'll ring you at nine in the morning".
  consequence  the turn states a plan the fact constrains.
               car in the garage until Friday -> "collect me on Wednesday".
  inference    near-zero overlap; only world knowledge connects them.
               vegan -> "I made lasagne, loads of bechamel".

THE CONTROL IS NOT OPTIONAL. Before printing a single score this runs the
shuffle from audit_control.py: each turn is scored against a *different*
fact's answer key. If the benchmark cannot separate the right fact from an
arbitrary one by a wide margin, it prints the failure and exits non-zero
without reporting accuracy. That is the whole lesson of the retraction, made
mechanical: an instrument that cannot fail visibly has not been shown to work.

    python turn_bench.py                       # 800 turns, daily-clean filler
    python turn_bench.py --turns 200 --budget 300 --budget 800
    python turn_bench.py --no-control          # refused; there is no such flag
"""

import argparse
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qontext-live"))

import qontext_memory as qm                                     # noqa: E402
from long_bench import (REPLIES, SUBJECTS, VERBS, TAILS,        # noqa: E402
                        daily_pairs, decoy)

# (statement, keywords proving the fact was carried, turn-shaped query, kind)
#
# Keywords are the WRITTEN key. A fact counts as carried when the pack
# contains one of them -- the same containment criterion eval_memory.py uses,
# and an upper bound on what a model could answer from the pack.
#
# The turn queries were written before any of them was run, and are not tuned:
# where a gap turned out to be uncrossable that is reported as zero rather
# than rewritten. Two of them (shellfish/seafood, vegan/bechamel) were chosen
# specifically because lexical retrieval *cannot* cross them, so the profile
# has a floor as well as a ceiling.
PAIRS = [
    ("I'm allergic to shellfish, badly.", ["shellfish"],
     "Fancy trying that new seafood place by the harbour tonight?",
     "hypernym"),
    ("I work as a nurse, mostly night shifts.", ["nurse", "night"],
     "I'll give you a ring around nine tomorrow morning then.", "script"),
    ("My brother Joris is getting married in June.", ["joris"],
     "Have you sorted out what you're wearing for your brother's thing?",
     "reference"),
    ("The car is in the garage until Friday.", ["garage"],
     "Could you come and collect me from the station on Wednesday?",
     "consequence"),
    ("I've been vegan for three years now.", ["vegan"],
     "I made a lasagne tonight, loads of bechamel, come over.", "inference"),
    ("I'm based in Utrecht, near the old canal.", ["utrecht"],
     "The conference is in Rotterdam - is that a long trip for you?",
     "consequence"),
    ("We track tasks in Trello.", ["trello"],
     "Where should I put the new bug I just found?", "script"),
    ("My dog is called Bikkel.", ["bikkel"],
     "We're away for the weekend - who's looking after him?", "script"),
    ("I'm terrified of flying, always have been.", ["flying", "terrified"],
     "The Berlin office wants us there Monday. Quickest route is two hours "
     "in the air.", "consequence"),
    ("Please keep explanations brief, I skim a lot.", ["brief", "skim"],
     "Can you walk me through how the caching layer works?", "script"),
    ("I don't drink, haven't for years.", ["drink"],
     "There's a wine tasting on Thursday, want to come?", "inference"),
    ("My daughter Nienke turns seven on the 14th.", ["nienke"],
     "I need to book something for the kids' party before next week.",
     "reference"),
    ("Our project is codenamed heron-nest.", ["heron"],
     "What should I call the repo when I set it up?", "script"),
    ("I'm doing a PhD in marine biology at Ghent.", ["marine", "ghent"],
     "How's the thesis coming along?", "reference"),
    ("People call me Marta.", ["marta"],
     "What is the user's name?", "quiz"),
    ("The sprint demo is on Friday at 10:00.", ["friday"],
     "When is the sprint demo?", "quiz"),
    ("The report is due March 3rd, hard deadline.", ["march"],
     "When is the report due?", "quiz"),
    ("Lately I code in Rust for fun.", ["rust"],
     "Which programming language does the user use?", "quiz"),
]

BUDGETS = (300, 800)
# Below this the benchmark is not reporting a measurement, it is reporting
# noise that happens to be shaped like one. Chosen before the first run.
MIN_SEPARATION = 5.0


def build(turns, seed, decoy_rate, filler):
    """Facts first, then filler -- the long_bench construction."""
    rnd = random.Random(seed)
    pairs = (daily_pairs(filler == "daily-clean")
             if filler.startswith("daily") else None)
    conversation = []
    for statement, _kw, _q, _kind in PAIRS:
        conversation.append(("user", statement))
        conversation.append(("assistant", rnd.choice(REPLIES)))
    while len(conversation) < turns:
        if rnd.random() < decoy_rate:
            conversation.append(("user", decoy(rnd)))
        elif pairs is not None:
            said, replied = rnd.choice(pairs)
            conversation.append(("user", said))
            conversation.append(("assistant", replied))
            continue
        else:
            conversation.append(("user", "%s %s %s." % (
                rnd.choice(SUBJECTS), rnd.choice(VERBS), rnd.choice(TAILS))))
        conversation.append(("assistant", rnd.choice(REPLIES)))
    return conversation[:turns]


def memory(conversation):
    mem = qm.QontextMemory(max_entries=10 ** 6)
    for speaker, text in conversation:
        mem.observe(speaker, text)
    return mem


def score(mem, budget, key_for):
    """key_for(i) -> keywords to look for when scoring pair i."""
    hits, by_kind = 0, {}
    for i, (_st, _kw, query, kind) in enumerate(PAIRS):
        packed = mem.pack(query, budget).lower()
        got = any(k in packed for k in key_for(i))
        hits += got
        slot = by_kind.setdefault(kind, [0, 0])
        slot[0] += got
        slot[1] += 1
    return hits, by_kind


def control(mem, budget, seeds):
    """Score every turn against a DIFFERENT fact's key. Must collapse."""
    totals = []
    for seed in seeds:
        rnd = random.Random(seed)
        def wrong(i, _rnd=rnd):
            other = [j for j in range(len(PAIRS)) if j != i]
            return PAIRS[_rnd.choice(other)][1]
        hits, _ = score(mem, budget, wrong)
        totals.append(hits)
    return statistics.mean(totals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=800)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--decoy-rate", type=float, default=0.10)
    ap.add_argument("--filler", default="daily-clean",
                    choices=("synthetic", "daily", "daily-clean"))
    ap.add_argument("--budget", type=int, action="append")
    ap.add_argument("--control-seeds", type=int, default=8)
    args = ap.parse_args()

    budgets = tuple(args.budget) if args.budget else BUDGETS
    seeds = list(range(1, args.control_seeds + 1))

    conversation = build(args.turns, args.seed, args.decoy_rate, args.filler)
    mem = memory(conversation)
    n = len(PAIRS)

    print("turn_bench: %d turns, %s filler, decoys %.0f%%, %d knots stored"
          % (len(conversation), args.filler, 100 * args.decoy_rate, len(mem)))
    print("%d planted facts, each with a written key and a turn that needs it"
          % n)

    # ------------------------------------------------------------- control
    print("\n" + "-" * 62)
    print("CONTROL (runs first; no score is printed if this fails)")
    print("-" * 62)
    print("%8s %10s %12s %12s" % ("budget", "real", "shuffled", "separation"))
    separations = {}
    for budget in budgets:
        real, _ = score(mem, budget, lambda i: PAIRS[i][1])
        shuf = control(mem, budget, seeds)
        sep = real / shuf if shuf else float("inf")
        separations[budget] = sep
        print("%8d %7d/%-3d %9.2f/%-3d %11s"
              % (budget, real, n, shuf, n,
                 "inf" if sep == float("inf") else "%.1fx" % sep))

    worst = min(separations.values())
    if worst < MIN_SEPARATION:
        print("\nFAILED: separation %.1fx is below the %.1fx bar.\n"
              "The benchmark cannot distinguish the right fact from an\n"
              "arbitrary one, so no accuracy number from it means anything.\n"
              "Not reporting scores." % (worst, MIN_SEPARATION))
        return 1
    print("\nPASSED: worst separation %.1fx (bar is %.1fx). Scores follow."
          % (worst, MIN_SEPARATION))

    # -------------------------------------------------------------- result
    for budget in budgets:
        hits, by_kind = score(mem, budget, lambda i: PAIRS[i][1])
        print("\n" + "-" * 62)
        print("budget %d: %d/%d facts carried  (%.0f%%)"
              % (budget, hits, n, 100.0 * hits / n))
        print("-" * 62)
        order = ["quiz", "hypernym", "reference", "script", "consequence",
                 "inference"]
        for kind in order:
            if kind in by_kind:
                got, total = by_kind[kind]
                print("  %-12s %d/%d" % (kind, got, total))
        turn_got = sum(v[0] for k, v in by_kind.items() if k != "quiz")
        turn_all = sum(v[1] for k, v in by_kind.items() if k != "quiz")
        print("  %-12s %d/%d  (%.0f%%)   <- the actual measurement"
              % ("TURN-SHAPED", turn_got, turn_all,
                 100.0 * turn_got / turn_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
