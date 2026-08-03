#!/usr/bin/env python3
"""Give the bridge a reserved seat instead of making it compete.

The affordance web reaches 18.5% of unreachable facts and converts 0.2% into
the pack. The diagnosis: a rescued knot ranks at median 35 while roughly 17
knots fit the budget. It is not buried -- it is just outside the door, losing
every time to knots the query matched lexically.

So stop making it compete. `PACK_RESERVE` already works on exactly this
principle for standing facts: a slice of the pack filled by a rule that
ignores the ranking. This does the same for the bridge -- N slots for the
best knots that ONLY the bridge could reach, taken before lexical ranking
spends the rest.

The cost is explicit: N slots spent on a candidate that is right some fraction
of the time. If that fraction is below what the displaced knots were worth,
this loses, and the measurement says so.

    python bridge_slots.py --web affordance_web.json --slots 0,1,2,3
"""
import argparse
import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW  # noqa: E402

BUDGET = 1200
EXCLUDED = {"log8.txt", "log12.txt"}


def pack_with_slots(plain, woven, turn, slots, budget=BUDGET):
    """Lexical pack, but the first `slots` places go to bridge-only knots."""
    lexical_order = [r for _s, r in plain._ranked(turn)]
    lexical_texts = {r["text"] for r in lexical_order}
    chosen, total = [], 0

    if slots:
        for _score, record in woven._ranked(turn):
            if record["text"] in lexical_texts:
                continue                       # the query reached it anyway
            cost = len(record["text"]) + (1 if chosen else 0)
            if total + cost > budget:
                continue
            chosen.append(record)
            total += cost
            if len(chosen) >= slots:
                break

    taken = {r["text"] for r in chosen}
    for record in lexical_order:
        if record["text"] in taken:
            continue
        cost = len(record["text"]) + (1 if chosen else 0)
        if total + cost > budget:
            continue
        chosen.append(record)
        total += cost
    return {r["text"] for r in chosen}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web")
    ap.add_argument("--weave", default="weave.qw")
    ap.add_argument("--slots", default="0,1,2,3")
    ap.add_argument("--logs")
    args = ap.parse_args()

    root = Path(args.logs) if args.logs else None
    if root is None or not root.is_dir():
        for guess in (Path(r"C:\Users\hylke\Documents\RP_Logs"),
                      Path.home() / "Documents" / "RP_Logs",
                      Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")):
            if guess.is_dir():
                root = guess
                break

    if args.web:
        from build_affordance_web import AffordanceWeb
        bridge = AffordanceWeb.load(args.web)
        label = "affordance web (%d words)" % len(bridge)
    else:
        from qontext_weave import WordWeave
        bridge = WordWeave.load(args.weave)
        label = "co-occurrence weave (%d words)" % len(bridge)
    print("bridge: %s\nlogs:   %s\n" % (label, root))

    slot_counts = [int(s) for s in args.slots.split(",") if s.strip()]
    carried = {n: 0 for n in slot_counts}
    needed_total = 0

    for path in sorted(root.glob("*.txt")):
        if path.name in EXCLUDED:
            continue
        turns = load(path)
        plain = qm.QontextMemory(max_entries=10**6, speakers="all")
        woven = qm.QontextMemory(max_entries=10**6, speakers="all", weave=bridge)
        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(plain):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(plain, reply, free)
                if needed:
                    needed_total += len(needed)
                    for n in slot_counts:
                        pack = pack_with_slots(plain, woven, text, n)
                        carried[n] += len(needed & pack)
            plain.observe("user" if is_user else speaker, text)
            woven.observe("user" if is_user else speaker, text)

    print("  %-22s %s" % ("reserved bridge slots", "needed facts carried"))
    base = None
    for n in slot_counts:
        share = 100.0 * carried[n] / max(1, needed_total)
        if base is None:
            base = share
        print("  %-22d %4d/%-5d  %5.2f%%   %+.2f"
              % (n, carried[n], needed_total, share, share - base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
