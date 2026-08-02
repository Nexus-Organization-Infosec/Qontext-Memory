#!/usr/bin/env python3
"""Is the bridging vocabulary present at write time, and lost by query time?

Three retrieval bridges converge on ~7% rescue, which says similarity search
cannot connect a turn to the fact it depends on. The proposed alternative is
to fix it earlier: store a richer representation so the lexical bridge exists
before anyone asks a question.

That relocates the problem rather than dissolving it -- inferring "cancelled"
from "didn't you have class though?" needs the same discourse understanding
the retriever lacked. But it relocates it somewhere better, IF the bridging
words are actually present in the turn's neighbourhood when the knot is made.

This is the oracle for that. For every unreachable needed fact, look at the
source turn and its neighbours, and ask whether the future query shares any
content word with that window. If yes, a context-aware extractor could have
captured the bridge. If no, the information was never there to capture and
richer extraction cannot help either.

No model, no embeddings: this measures what is *available*, which is the
ceiling on any write-time method however clever.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW  # noqa: E402

LOGS = ("log13.txt", "log1.txt", "log5.txt", "log2.txt", "log6.txt")
WINDOWS = (0, 1, 2, 4)      # turns of context around the one that made the knot


def main():
    root = Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")
    unreachable = 0
    bridged = {w: 0 for w in WINDOWS}

    for name in LOGS:
        path = root / name
        if not path.is_file():
            continue
        turns = load(path)
        mem = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
        origin = {}                       # knot text -> index of the turn that made it

        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(mem):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(mem, reply, free)
                if needed:
                    lexical = {r["text"] for _s, r in mem._ranked(text)}
                    query = content_words(text)
                    for knot in needed:
                        if knot in lexical or knot not in origin:
                            continue
                        unreachable += 1
                        made_at = origin[knot]
                        for w in WINDOWS:
                            lo, hi = max(0, made_at - w), min(len(turns), made_at + w + 1)
                            around = set()
                            for _, _, nearby in turns[lo:hi]:
                                around |= content_words(nearby)
                            if query & around:
                                bridged[w] += 1
            added = mem.observe("user" if is_user else speaker, text)
            for knot in added:
                origin.setdefault(knot, i)

    print("unreachable needed facts with a known origin: %d\n" % unreachable)
    print("  context around the knot's source turn   query shares a word")
    for w in WINDOWS:
        label = "the source turn only" if w == 0 else "+/- %d turns" % w
        print("  %-38s %4d  (%.1f%%)"
              % (label, bridged[w], 100.0 * bridged[w] / max(1, unreachable)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
