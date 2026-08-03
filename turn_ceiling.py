#!/usr/bin/env python3
"""What is the best any packer could do on the turn benchmark?

Every strategy measured so far lands between 4.3% (lexical) and 16.6%
(coverage). Before building a seventh mechanism it is worth knowing what the
number would be if selection were *perfect* -- because the answer decides
which problem we actually have:

    ceiling ~100%  ->  a selection problem. The facts fit; nothing finds them.
    ceiling ~20%   ->  a budget problem. No retriever can send what will not
                       fit, and the work belongs in compression, not ranking.
    ceiling ~5%    ->  a metric problem. The benchmark is asking for something
                       unachievable and every number derived from it is noise.

The oracle packs the needed knots first, cheapest-first so the budget buys as
many as possible, then reports what fraction fit. It is allowed to cheat
completely: it knows the answer key. That is the point.

Also reported, because they constrain the ceiling differently:

  * needed knots per turn, and how many knots the budget holds
  * how many needed knots are lexically reachable (score > 0) at all
  * the length of needed knots against the store average -- if the facts a
    reply draws on are systematically long, the budget binds harder than the
    per-turn count suggests

    python turn_ceiling.py /path/to/RP_Logs --budget 1200
"""

import argparse
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import (content_words, needed_knots,         # noqa: E402
                          RECENCY_WINDOW, EXCLUDED)


def oracle_pack(needed, budget):
    """Fit as many needed knots as possible. Cheapest first is optimal here:
    all items have equal value, so maximising count means minimising cost."""
    got, total = 0, 0
    for text in sorted(needed, key=len):
        cost = len(text) + (1 if got else 0)
        if total + cost > budget:
            break
        got += 1
        total += cost
    return got


def run(path, budget):
    turns = load(path)
    mem = qm.QontextMemory(max_entries=10 ** 6, speakers="all")

    need = fit = reachable = 0
    per_turn, needed_lens, pack_counts = [], [], []

    for i, (_speaker, is_user, text) in enumerate(turns):
        reply = turns[i + 1][2] if i + 1 < len(turns) else None
        if is_user and reply and len(mem):
            free = set()
            for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                free |= content_words(earlier)
            needed = needed_knots(mem, reply, free)
            if needed:
                need += len(needed)
                per_turn.append(len(needed))
                needed_lens.extend(len(t) for t in needed)
                fit += oracle_pack(needed, budget)
                scored = {k["text"] for s, k in mem._ranked(text) if s[0] > 0}
                reachable += len(needed & scored)
                pack_counts.append(len(mem.pack(text, budget).split("\n")))
        mem.observe("user" if is_user else _speaker, text)

    store_lens = [len(t) for t in mem.entries()]
    return {
        "name": path.name, "need": need, "fit": fit, "reachable": reachable,
        "per_turn": statistics.mean(per_turn) if per_turn else 0.0,
        "needed_len": statistics.mean(needed_lens) if needed_lens else 0.0,
        "store_len": statistics.mean(store_lens) if store_lens else 0.0,
        "pack_knots": statistics.mean(pack_counts) if pack_counts else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs")
    ap.add_argument("--budget", type=int, default=1200)
    args = ap.parse_args()

    rows = []
    for path in sorted(Path(args.logs).glob("*.txt")):
        if path.name in EXCLUDED:
            continue
        row = run(path, args.budget)
        if row["need"]:
            rows.append(row)

    print("budget %d characters\n" % args.budget)
    print("%-12s %6s %8s %10s %8s %8s %8s"
          % ("log", "needed", "oracle", "reachable", "need/turn",
             "len(need)", "knots"))
    for r in rows:
        print("%-12s %6d %7.1f%% %9.1f%% %8.1f %8.0f %8.1f"
              % (r["name"], r["need"], 100.0 * r["fit"] / r["need"],
                 100.0 * r["reachable"] / r["need"], r["per_turn"],
                 r["needed_len"], r["pack_knots"]))

    need = sum(r["need"] for r in rows)
    fit = sum(r["fit"] for r in rows)
    reach = sum(r["reachable"] for r in rows)
    print("\npooled across %d logs, %d needed facts" % (len(rows), need))
    print("  oracle ceiling      %5.1f%%   (perfect selection, same budget)"
          % (100.0 * fit / need))
    print("  lexically reachable %5.1f%%   (any nonzero score)"
          % (100.0 * reach / need))
    print("  needed knot length  %5.0f chars vs %.0f average in store"
          % (statistics.mean([r["needed_len"] for r in rows]),
             statistics.mean([r["store_len"] for r in rows])))
    print("  knots per pack      %5.1f"
          % statistics.mean([r["pack_knots"] for r in rows]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
