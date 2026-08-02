#!/usr/bin/env python3
"""Why did the weave fail? Four hypotheses, measured.

The cross-log test said a WikiText-103 weave was neutral to slightly harmful
at every setting. That is a result, not an explanation, and the four candidate
explanations suggest completely different next steps:

  1. false positives      the weave admits knots that are simply wrong
  2. budget displacement  it admits plausible knots that push out better ones
  3. signal swamping      association scores overwhelm lexical ones
  4. related-but-useless  it retrieves genuinely related facts nobody needed

This replays real turns with the weave on and off, and records what changed.

    python diagnose_weave.py /path/to/logs --weave weave.qw --only log13.txt
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import qontext_memory as qm          # noqa: E402
from rp_probe import load            # noqa: E402
from rp_turnbench import (content_words, needed_knots, RECENCY_WINDOW)  # noqa: E402


def run(path, weave, budget):
    turns = load(path)
    plain = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
    woven = qm.QontextMemory(max_entries=10 ** 6, speakers="all", weave=weave)

    stats = {"turns": 0, "changed": 0, "gained": 0, "lost": 0,
             "gained_needed": 0, "lost_needed": 0,
             "conf_when_changed": [], "conf_when_same": [],
             "assoc_tokens": 0}

    for i, (speaker, is_user, text) in enumerate(turns):
        reply = turns[i + 1][2] if i + 1 < len(turns) else None
        if is_user and reply and len(plain):
            free = set()
            for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                free |= content_words(earlier)
            needed = needed_knots(plain, reply, free)
            if needed:
                stats["turns"] += 1
                a = set(plain.pack(text, budget).split("\n"))
                b = set(woven.pack(text, budget).split("\n"))
                # lexical confidence: the best score the query could reach
                ranked = plain._ranked(text)
                top = ranked[0][0][0] if ranked else 0.0
                expanded, associated = woven._expand(text)
                stats["assoc_tokens"] += len(associated)
                if a != b:
                    stats["changed"] += 1
                    stats["conf_when_changed"].append(top)
                    gained, lost = b - a, a - b
                    stats["gained"] += len(gained)
                    stats["lost"] += len(lost)
                    stats["gained_needed"] += len(gained & needed)
                    stats["lost_needed"] += len(lost & needed)
                else:
                    stats["conf_when_same"].append(top)
        plain.observe("user" if is_user else speaker, text)
        woven.observe("user" if is_user else speaker, text)
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs")
    ap.add_argument("--weave", required=True)
    ap.add_argument("--budget", type=int, default=1200)
    ap.add_argument("--only", action="append")
    args = ap.parse_args()

    from qontext_weave import WordWeave
    weave = WordWeave.load(args.weave)
    print("weave: %d words" % len(weave))

    root = Path(args.logs)
    excluded = {"log8.txt", "log12.txt"}
    files = ([root / n for n in args.only] if args.only
             else sorted(p for p in root.glob("*.txt")))
    files = [p for p in files if p.name not in excluded]

    total = {}
    for path in files:
        s = run(path, weave, args.budget)
        for k, v in s.items():
            if isinstance(v, list):
                total.setdefault(k, []).extend(v)
            else:
                total[k] = total.get(k, 0) + v
        print("  %-12s %d turns, %d changed" % (path.name, s["turns"],
                                                s["changed"]))

    turns = max(1, total["turns"])
    changed = max(1, total["changed"])
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    print("\n%d scored turns, weave changed the pack on %d (%.0f%%)"
          % (total["turns"], total["changed"], 100.0 * total["changed"] / turns))
    print("association tokens added per turn: %.2f"
          % (total["assoc_tokens"] / turns))
    print("\nwhen the pack changed:")
    print("  knots gained %d, of which needed %d (%.0f%% useful)"
          % (total["gained"], total["gained_needed"],
             100.0 * total["gained_needed"] / max(1, total["gained"])))
    print("  knots lost   %d, of which needed %d (%.0f%% harmful)"
          % (total["lost"], total["lost_needed"],
             100.0 * total["lost_needed"] / max(1, total["lost"])))
    print("\nlexical confidence (top score the query could reach on its own):")
    print("  when the weave changed the pack: %.2f" % mean(total["conf_when_changed"]))
    print("  when it changed nothing:         %.2f" % mean(total["conf_when_same"]))
    net = total["gained_needed"] - total["lost_needed"]
    print("\nnet needed facts: %+d" % net)
    return 0


if __name__ == "__main__":
    sys.exit(main())
