#!/usr/bin/env python3
"""Judge a semantic bridge on the population it exists to serve.

Aggregate accuracy hides the mechanism. 80% of missed facts are unreachable --
zero lexical score, never candidates at all -- so the only question that
matters for a bridge is:

    of the unreachable facts, how many does it make reachable,
    and what does that cost in candidate-pool inflation?

A bridge that rescues nothing was never going to help however it is fused. A
bridge that rescues many but inflates the pool tenfold has a fusion problem,
not a reach problem. Those need different fixes, and overall accuracy cannot
tell them apart.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW  # noqa: E402
from qontext_weave import WordWeave                            # noqa: E402

BUDGET = 1200


def main():
    root = Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")
    names = ("log13.txt", "log1.txt", "log5.txt", "log2.txt", "log6.txt")
    weave = WordWeave.load("weave.qw")

    unreachable = rescued = rescued_into_pack = 0
    pool_plain = pool_woven = turns_counted = 0

    for name in names:
        turns = load(root / name)
        plain = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
        woven = qm.QontextMemory(max_entries=10 ** 6, speakers="all", weave=weave)
        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(plain):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(plain, reply, free)
                if needed:
                    turns_counted += 1
                    lex = {r["text"] for _s, r in plain._ranked(text)}
                    wov = {r["text"]: n for n, (_s, r)
                           in enumerate(woven._ranked(text))}
                    pool_plain += len(lex)
                    pool_woven += len(wov)
                    pack = set(woven.pack(text, BUDGET).split("\n"))
                    for knot in needed:
                        if knot in lex:
                            continue                    # was reachable already
                        unreachable += 1
                        if knot in wov:
                            rescued += 1
                            if knot in pack:
                                rescued_into_pack += 1
            plain.observe("user" if is_user else speaker, text)
            woven.observe("user" if is_user else speaker, text)

    print("unreachable needed facts: %d\n" % unreachable)
    print("  made reachable by the weave   %4d  (%.1f%%)"
          % (rescued, 100.0 * rescued / max(1, unreachable)))
    print("  and actually reached the pack %4d  (%.1f%%)"
          % (rescued_into_pack, 100.0 * rescued_into_pack / max(1, unreachable)))
    print("\ncandidate pool per turn: lexical %.0f -> woven %.0f  (%.1fx)"
          % (pool_plain / max(1, turns_counted), pool_woven / max(1, turns_counted),
             pool_woven / max(1, pool_plain)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
