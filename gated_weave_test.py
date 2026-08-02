#!/usr/bin/env python3
"""Late fusion: use the weave only when lexical retrieval is weak.

The diagnosis said the weave intervenes on 56% of turns and fires *hardest*
where lexical confidence is already highest -- the opposite of where it is
needed. This tests an explicit gate: fall back to semantic expansion only when
the query's own words reach nothing much, and cap how far it may reach.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW  # noqa: E402
from qontext_weave import WordWeave                            # noqa: E402

BUDGET = 1200


def measure(paths, weave, gates, cap):
    plainw = {g: [0, 0] for g in gates}     # gate -> [needed, carried]
    always = [0, 0]
    never = [0, 0]
    for path in paths:
        turns = load(path)
        a = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
        b = qm.QontextMemory(max_entries=10 ** 6, speakers="all", weave=weave)
        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(a):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(a, reply, free)
                if needed:
                    pa = set(a.pack(text, BUDGET).split("\n"))
                    pb = set(b.pack(text, BUDGET).split("\n"))
                    ranked = a._ranked(text)
                    top = ranked[0][0][0] if ranked else 0.0
                    never[0] += len(needed); never[1] += len(needed & pa)
                    always[0] += len(needed); always[1] += len(needed & pb)
                    for g in gates:
                        chosen = pb if top < g else pa
                        plainw[g][0] += len(needed)
                        plainw[g][1] += len(needed & chosen)
            a.observe("user" if is_user else speaker, text)
            b.observe("user" if is_user else speaker, text)
    return never, always, plainw


def main():
    root = Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")
    paths = [root / n for n in ("log13.txt", "log1.txt", "log5.txt")]
    weave = WordWeave.load("weave.qw")
    gates = (1.0, 2.0, 3.0, 4.0)
    never, always, gated = measure(paths, weave, gates, cap=3)
    pct = lambda p: 100.0 * p[1] / max(1, p[0])
    print("needed facts carried, budget %d, 3 logs\n" % BUDGET)
    print("  %-28s %4d/%-4d  %.1f%%" % ("lexical only (no weave)", never[1], never[0], pct(never)))
    print("  %-28s %4d/%-4d  %.1f%%" % ("weave always on", always[1], always[0], pct(always)))
    for g in gates:
        p = gated[g]
        print("  %-28s %4d/%-4d  %.1f%%" % ("weave only when top < %.1f" % g, p[1], p[0], pct(p)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
