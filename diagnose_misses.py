#!/usr/bin/env python3
"""Recall or discrimination? For every needed fact the pack missed, ask why.

Two failure modes with completely different fixes:

  unreachable   the knot shares no word with the query, so ranking never sees
                it at all. A semantic bridge is the only thing that helps.
  outbid        the knot IS ranked, but other knots beat it to the budget.
                Better discrimination helps; more candidates make it worse.

The distinction decides whether the next thing to build is a retriever or a
ranker, so it is worth measuring rather than assuming.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW  # noqa: E402

BUDGET = 1200


def main():
    root = Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")
    names = ("log13.txt", "log1.txt", "log5.txt", "log2.txt", "log6.txt")
    unreachable = outbid = carried = 0
    ranks = []
    for name in names:
        turns = load(root / name)
        mem = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(mem):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(mem, reply, free)
                if needed:
                    pack = set(mem.pack(text, BUDGET).split("\n"))
                    ranked = mem._ranked(text)
                    order = {r["text"]: n for n, (_s, r) in enumerate(ranked)}
                    for knot in needed:
                        if knot in pack:
                            carried += 1
                        elif knot in order:
                            outbid += 1
                            ranks.append(order[knot])
                        else:
                            unreachable += 1
            mem.observe("user" if is_user else speaker, text)

    total = carried + outbid + unreachable
    print("needed facts: %d\n" % total)
    print("  carried by the pack      %4d  (%.1f%%)" % (carried, 100.0*carried/total))
    print("  ranked but outbid        %4d  (%.1f%%)  <- discrimination" % (outbid, 100.0*outbid/total))
    print("  unreachable, score zero  %4d  (%.1f%%)  <- no lexical bridge" % (unreachable, 100.0*unreachable/total))
    if ranks:
        ranks.sort()
        mid = ranks[len(ranks)//2]
        print("\n  when outbid, median rank %d; %d%% ranked in the top 20"
              % (mid, 100*sum(1 for r in ranks if r < 20)//len(ranks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
