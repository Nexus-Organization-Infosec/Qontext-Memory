#!/usr/bin/env python3
"""
Measures supersession: does a corrected fact replace the stale one, and does
anything ever collapse two facts that should both survive?

    python eval_supersede.py                 # score the current module
    python eval_supersede.py --against FILE  # compare with another version

Three measurements:

  corrections   pairs where the second statement corrects the first. The
                second must win and the first must be gone.
  safety        hand-picked pairs that share vocabulary but are different
                facts. Both must survive. Any loss here is a bug, not a
                trade-off.
  invariant     every pair of the distinct facts within conversations A, B
                and C, cross-multiplied. None of them corrects another, so zero
                collapses are allowed. This is the real guarantee — the
                hand-picked list only covers what I thought to write down.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import conversations as cv  # noqa: E402

# (stale statement, corrected statement, what must be recalled, what must not)
CORRECTIONS = [
    ("I live in Rotterdam these days.",
     "Actually I'm in Antwerp now, I moved last month.",
     "antwerp", "rotterdam"),
    ("My manager is Priya.",
     "My manager is Tomas now, Priya left.",
     "tomas", "priya"),
    ("I code in Rust mostly.",
     "These days I code in Go, not Rust.",
     "go", "rust"),
    ("The demo is on Friday at 10:00.",
     "The demo moved to Tuesday at 09:00.",
     "tuesday", "friday"),
    ("I work at Kestrel Analytics.",
     "I work at Vandermeer BV now.",
     "vandermeer", "kestrel"),
    ("My editor is Neovim.",
     "My editor is Helix these days.",
     "helix", "neovim"),
    ("We track issues in Jira.",
     "We track issues in Linear now.",
     "linear", "jira"),
    ("The report is due March 3rd.",
     "The report is due April 7th instead.",
     "april", "march"),
    ("People call me Marta.",
     "People call me Martje, actually.",
     "martje", "marta"),
    ("My repo is called heron-nest.",
     "My repo is called willow-basket now.",
     "willow", "heron"),
    ("I prefer long explanations.",
     "I prefer short explanations.",
     "short", "long"),
]

# Pairs that share vocabulary but are different facts. Both must survive.
MUST_BOTH_SURVIVE = [
    ("My dog is called Bikkel.", "My cat is called Muis."),
    ("My daughter is called Lotte.", "My brother is called Sander."),
    ("My manager is Priya.", "My supervisor is Professor Aaltink."),
    ("I live in Antwerp.", "My mother lives in Rotterdam."),
    ("The demo is on Friday at 10:00.", "The retro is on Friday at 16:00."),
    ("The report is due March 3rd.", "The invoice is due March 25th."),
    ("I code in Rust.", "I write documentation in Markdown."),
    ("We deploy on Fly.io.", "We store everything in Postgres."),
    ("My laptop is a ThinkPad.", "My phone is a Pixel."),
    ("I speak Dutch and English.", "I am learning Portuguese."),
    ("The staging server is behind the VPN.",
     "The production server is on Fly.io."),
    ("Our client is Vandermeer BV.", "Our competitor is Nachtvogel."),
    ("I train on Tuesdays.", "I play bass on Thursdays."),
    ("My birthday is the 22nd of November.",
     "My daughter's birthday is the 3rd of June."),
    ("Answer me in bullet points.", "Write to me in English."),
    ("I am allergic to shellfish.", "I am vegetarian."),
    ("The kickoff is on Wednesday.", "The standup is at 09:15."),
    ("My GPU is AMD.", "My CPU is Intel."),
    ("I use Neovim for code.", "I use Figma for design."),
    ("The budget is 12000 euro.", "Parking costs 4 euro an hour."),
    # Parallel facts distinguished *only* by a number attached to an entity.
    # The number is the identifier here, not a changeable payload.
    ("The manager of team 5 is unavailable.",
     "The manager of team 7 is unavailable."),
    ("Room 12 has the good projector.", "Room 14 has the broken projector."),
    ("Flight KL1234 leaves from gate B.", "Flight KL5678 leaves from gate C."),
    ("Sprint 3 was a disaster.", "Sprint 4 went fine."),
    ("Server node 2 is the primary.", "Server node 9 is the replica."),
    # scale words describe different subjects: not the same preference
    ("I like short meetings.", "I like long walks."),
    ("Keep the summary brief.", "Keep the appendix detailed."),
]


def load(path):
    spec = importlib.util.spec_from_file_location("qm_supersede_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score_corrections(QM):
    """Superseded means: the stale *knot* is gone and the new value is there.

    Checking for the stale word anywhere in the memory is not the same test —
    "These days I code in Go, not Rust" mentions the old value in the
    correction itself, and that knot is the right one to keep.
    """
    fixed, stale_kept, detail = 0, 0, []
    for before, after, want, unwanted in CORRECTIONS:
        stale = QM()
        stale.observe("user", before)
        stale_knots = set(stale.entries())

        mem = QM()
        mem.observe("user", before)
        mem.observe("user", after)
        knots = mem.entries()
        has_new = want in " ".join(knots).lower()
        has_old = bool(stale_knots & set(knots))
        if has_new and not has_old:
            fixed += 1
        else:
            detail.append((after, want, unwanted, has_new, has_old))
        if has_old:
            stale_kept += 1
    return fixed, stale_kept, detail


def score_safety(QM):
    lost = []
    for first, second in MUST_BOTH_SURVIVE:
        mem = QM()
        mem.observe("user", first)
        mem.observe("user", second)
        if len(mem.entries()) < 2:
            lost.append((first, second, mem.entries()))
    return lost


def score_invariant(mod):
    """Cross-multiply the facts *within* each conversation: none may
    supersede another.

    Within one conversation every planted fact is distinct by construction —
    that is what makes this a valid invariant. Across conversations it would
    not be: A's speaker lives in Utrecht and B's lives in Groningen, and
    those two knots colliding is correct behaviour, not a bug.
    """
    QM = mod.QuipuMemory
    collapses, pairs, total = [], 0, 0
    for conv in (cv.CONV_A, cv.CONV_B, cv.CONV_C):
        knots = []
        for speaker, text in conv:
            if speaker != "user":
                continue
            mem = QM()
            mem.observe("user", text)
            knots.extend(mem.entries())
        knots = list(dict.fromkeys(knots))
        total += len(knots)
        for i, a in enumerate(knots):
            for b in knots[i + 1:]:
                pairs += 1
                mem = QM()
                mem.add(a)
                mem.add(b)
                if len(mem.entries()) < 2:
                    collapses.append((a, b))
    return total, pairs, collapses


def report(label, mod):
    QM = mod.QuipuMemory
    fixed, stale, detail = score_corrections(QM)
    lost = score_safety(QM)
    n_knots, pairs, collapses = score_invariant(mod)

    print("== %s" % label)
    print("  corrections  %2d/%-2d superseded   (%d stale facts still packed)"
          % (fixed, len(CORRECTIONS), stale))
    print("  safety       %2d/%-2d pairs kept apart"
          % (len(MUST_BOTH_SURVIVE) - len(lost), len(MUST_BOTH_SURVIVE)))
    print("  invariant    %d collapses across %d pairs of %d distinct facts"
          % (len(collapses), pairs, n_knots))
    for text, want, unwanted, has_new, has_old in detail:
        print("     MISS %-46s new=%s old=%s" % (text[:46], has_new, has_old))
    for first, second, kept in lost:
        print("     COLLAPSED %r + %r -> %r" % (first[:30], second[:30], kept))
    for a, b in collapses[:10]:
        print("     INVARIANT BREAK %r + %r" % (a[:44], b[:44]))
    print()
    return fixed, len(lost), len(collapses)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default=str(HERE / "qontext_memory.py"))
    ap.add_argument("--against")
    args = ap.parse_args()

    if args.against:
        report("baseline: %s" % args.against, load(args.against))
    fixed, lost, collapses = report("current: %s" % args.module,
                                    load(args.module))
    ok = fixed >= 8 and lost == 0 and collapses == 0
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
