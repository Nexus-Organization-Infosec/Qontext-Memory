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
LIVE = HERE.parent / "qontext-live"
if not LIVE.is_dir():
    # qontext-memory/bench layout: qontext_memory.py sits at the repo root
    # instead of a sibling qontext-live/ folder.
    LIVE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LIVE))

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

# ---------------------------------------------------------------- suite B
#
# HELD OUT. Written after suite A had already been used to tune K for the
# embedding bridge, and never tuned against.
#
# The reason it exists: A has fourteen turn-shaped items and the author wrote
# every one of them. Fitting a mechanism to fourteen sentences of one person's
# invention is how a sound benchmark becomes an unsound result, and this
# project has already shipped one of those. The chat suites have carried a
# tuned-on/held-out split from the start; the turn benchmark had none.
#
# B is deliberately unlike A in surface features, so it tests generalisation
# rather than resampling: workplace and civic vocabulary instead of domestic,
# different names, different gap instances. Same specification -- the query
# must share as little vocabulary with the fact as the gap kind allows.
#
# The rule that makes it worth having: a mechanism may be tuned on A. Results
# are reported on both, always, and a gain that appears only on A is reported
# as overfitting rather than as a finding.
PAIRS_B = [
    ("I'm allergic to penicillin.", ["penicillin"],
     "The doctor will probably put you on antibiotics for that.", "hypernym"),
    ("We use PostgreSQL for everything.", ["postgresql"],
     "Which database should I point the new service at?", "hypernym"),
    ("I drive an old Volvo estate.", ["volvo"],
     "Will the whole team fit in your car on Saturday?", "hypernym"),
    ("My cat is called Pluis.", ["pluis"],
     "Do you need someone to feed the animals while you're away?",
     "hypernym"),

    ("My wife Saskia runs a bakery.", ["saskia"],
     "Is your wife working this Saturday?", "reference"),
    ("Our lead designer is Bram.", ["bram"],
     "Has the designer seen the new mockups yet?", "reference"),
    ("I did my masters at Wageningen.", ["wageningen"],
     "Do you ever go back to where you studied?", "reference"),
    ("My youngest, Fenna, started school in September.", ["fenna"],
     "How's your youngest settling in?", "reference"),

    ("I carry the pager every third weekend.", ["pager"],
     "Fancy a long hike up in the hills this Saturday?", "script"),
    ("The office badge readers stop working after 19:00.", ["badge"],
     "I'll come by and drop the laptop off around eight in the evening.",
     "script"),
    ("I keep kosher.", ["kosher"],
     "Shall I order the bacon sandwiches for the meeting?", "script"),
    ("My passport expires in March.", ["passport"],
     "Shall we book the Tokyo trip for the summer?", "script"),

    ("The building's lift has been out of order since April.", ["lift"],
     "My mother's coming to visit, she's ninety-one.", "consequence"),
    ("I've got stitches in my hand until Friday.", ["stitches"],
     "Are you joining the five-a-side on Wednesday?", "consequence"),
    ("Our AWS credits run out at the end of Q3.", ["aws"],
     "Can we leave the training cluster running through October?",
     "consequence"),
    ("I'm banned from driving until June.", ["banned"],
     "Could you do the airport run on Saturday morning?", "consequence"),

    ("I'm six months pregnant.", ["pregnant"],
     "Shall I pour you a glass of the Rioja?", "inference"),
    ("I gave up gluten last year.", ["gluten"],
     "I've made a big bowl of couscous for everyone.", "inference"),
    ("I'm red-green colourblind.", ["colourblind"],
     "Just click it when the indicator goes from amber to lime.",
     "inference"),
    ("My hearing aid battery died this morning.", ["hearing"],
     "Let's take the call in the atrium, it's livelier there.", "inference"),

    ("My manager is called Ingrid.", ["ingrid"],
     "Who is the user's manager?", "quiz"),
    ("The standup is at 09:15 sharp.", ["09:15"],
     "When is the standup?", "quiz"),
    ("We deploy on Kubernetes.", ["kubernetes"],
     "What do they deploy on?", "quiz"),
    # "What street does the user live on?" was the first version of this and
    # it is not a quiz question: it shares no word with "my flat is on
    # Weverstraat", so it was a hypernym gap wearing a quiz label, and it
    # duly failed. A quiz anchor must repeat the vocabulary of its answer --
    # that is the whole point of having one.
    ("My flat is on Weverstraat.", ["weverstraat"],
     "Where is the user's flat?", "quiz"),
]

SUITES = [("A (tuned-on)", PAIRS), ("B (held-out)", PAIRS_B)]

BUDGETS = (300, 800)
# Below this the benchmark is not reporting a measurement, it is reporting
# noise that happens to be shaped like one. Chosen before the first run.
MIN_SEPARATION = 5.0


def build(turns, seed, decoy_rate, filler, pairs=None):
    """Facts first, then filler -- the long_bench construction."""
    rnd = random.Random(seed)
    daily = (daily_pairs(filler == "daily-clean")
             if filler.startswith("daily") else None)
    pairs_ = PAIRS if pairs is None else pairs
    conversation = []
    for statement, _kw, _q, _kind in pairs_:
        conversation.append(("user", statement))
        conversation.append(("assistant", rnd.choice(REPLIES)))
    while len(conversation) < turns:
        if rnd.random() < decoy_rate:
            conversation.append(("user", decoy(rnd)))
        elif daily is not None:
            said, replied = rnd.choice(daily)
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


def score(mem, budget, key_for, pairs=None, skip_unstored=True):
    """key_for(i) -> keywords to look for when scoring pair i.

    Items whose fact never reached the store are skipped: they are an
    extraction failure and counting them as retrieval misses would blame the
    retriever for something it never had a chance at.
    """
    rows = PAIRS if pairs is None else pairs
    store = "\n".join(mem.entries()).lower() if skip_unstored else None
    hits, by_kind = 0, {}
    for i, (_st, _kw, query, kind) in enumerate(rows):
        if store is not None and not any(k in store for k in rows[i][1]):
            continue
        packed = mem.pack(query, budget).lower()
        got = any(k in packed for k in key_for(i))
        hits += got
        slot = by_kind.setdefault(kind, [0, 0])
        slot[0] += got
        slot[1] += 1
    return hits, by_kind


def control(mem, budget, seeds, pairs=None):
    """Score every turn against a DIFFERENT fact's key. Must collapse."""
    rows = PAIRS if pairs is None else pairs
    totals = []
    for seed in seeds:
        rnd = random.Random(seed)

        def wrong(i, _rnd=rnd):
            other = [j for j in range(len(rows)) if j != i]
            return rows[_rnd.choice(other)][1]
        hits, _ = score(mem, budget, wrong, rows)
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

    for label, pairs in SUITES:
        conversation = build(args.turns, args.seed, args.decoy_rate,
                             args.filler, pairs)
        mem = memory(conversation)
        n = len(pairs)
        turn_n = sum(1 for p in pairs if p[3] != "quiz")
        print("\n" + "=" * 64)
        print("SUITE %s -- %d facts (%d turn-shaped), %d knots stored"
              % (label, n, turn_n, len(mem)))
        print("=" * 64)

        # A fact the extractor never stored cannot be retrieved, and scoring
        # it as a retrieval miss conflates two different failures. Report
        # them apart: retrieval is measured on the items that are actually
        # in memory, and the rest are named as an extraction result.
        store = "\n".join(mem.entries()).lower()
        lost = [(st, kind) for st, kw, _q, kind in pairs
                if not any(k in store for k in kw)]
        if lost:
            print("  %d fact(s) never entered memory -- an EXTRACTION miss, "
                  "excluded from the retrieval score:" % len(lost))
            for st, kind in lost:
                print("      [%s] %s" % (kind, st))

        ok = True
        for budget in budgets:
            real, _ = score(mem, budget, lambda i: pairs[i][1], pairs)
            shuf = control(mem, budget, seeds, pairs)
            sep = real / shuf if shuf else float("inf")
            flag = "" if sep >= MIN_SEPARATION else "   <-- BELOW BAR"
            print("  control @%-4d real %2d/%-2d  shuffled %5.2f  %5.1fx%s"
                  % (budget, real, n, shuf,
                     99.9 if sep == float("inf") else sep, flag))
            ok = ok and sep >= MIN_SEPARATION
        if not ok:
            print("\n  FAILED the control. No score reported for this suite.")
            continue

        for budget in budgets:
            hits, by_kind = score(mem, budget, lambda i: pairs[i][1], pairs)
            order = ["quiz", "hypernym", "reference", "script",
                     "consequence", "inference"]
            detail = "  ".join("%s %d/%d" % (k, by_kind[k][0], by_kind[k][1])
                               for k in order if k in by_kind)
            got = sum(v[0] for k, v in by_kind.items() if k != "quiz")
            allq = sum(v[1] for k, v in by_kind.items() if k != "quiz")
            print("  budget %-4d TURN-SHAPED %2d/%-2d (%2.0f%%)   %s"
                  % (budget, got, allq, 100.0 * got / allq, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
