#!/usr/bin/env python3
"""Does the shuffle control work at all?

audit_needed.py used a shuffle to condemn the turn benchmark: pairing a turn
with the *wrong* reply marked 94% as many facts "needed" as the right one, so
the metric was measuring vocabulary coincidence rather than dependence.

That conclusion is only worth something if the control can tell the
difference. A control that reports "shuffled = real" no matter what it is
pointed at proves nothing -- it would condemn a perfectly good benchmark
just as loudly. Five harness bugs in this project produced false findings;
there is no reason to assume the sixth instrument is the trustworthy one.

So: run the identical shuffle against the chat suites, where the ground truth
is a hand-written answer key rather than a proxy. For each question, score the
pack against the keywords of a *different* question.

    sound metric   -> real >> shuffled.  The pack for "what is the user's
                      name" should not contain the answer to "what editor do
                      they use".
    broken control -> real == shuffled here too, and the turn verdict is void.

Also re-runs the turn shuffle across several seeds, since the original verdict
rested on one.

    python audit_control.py
    python audit_control.py --logs /path/to/RP_Logs --seeds 5
"""

import argparse
import random
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "qontext-run"))

import qontext_memory as qm     # noqa: E402
import bench_test as bt         # noqa: E402
import stress_conv as sc        # noqa: E402

SUITES = [
    ("A  (tuned-on)", bt.CONV_A, bt.QUESTIONS_A),
    ("B  (held-out)", bt.CONV_B, bt.QUESTIONS_B),
    ("C  (stress)  ", sc.CONV_C, sc.QUESTIONS_C),
]
BUDGETS = (300, 800)


def build(conv):
    m = qm.QontextMemory()
    for speaker, text in conv:
        m.observe(speaker, text)
    return m


def chat_shuffle(seeds):
    """Real vs shuffled hit rate on the hand-keyed suites."""
    print("=" * 68)
    print("CONTROL 1  the same shuffle, against a hand-written answer key")
    print("=" * 68)
    print("\nIf the shuffle is a working instrument, `shuffled` collapses "
          "here.\n")
    print("%-16s %7s %10s %12s" % ("suite", "budget", "real", "shuffled"))

    totals = {b: [0, 0, 0] for b in BUDGETS}   # hits, shuffled hits, asked
    for label, conv, questions in SUITES:
        mem = build(conv)
        for budget in BUDGETS:
            real = shuf = 0
            for i, (question, keywords) in enumerate(questions):
                packed = mem.pack(question, budget).lower()
                if any(k in packed for k in keywords):
                    real += 1
                # the keywords of some other question in the same suite
                for seed in seeds:
                    rng = random.Random(seed * 1000 + i)
                    others = [kw for j, (_q, kw) in enumerate(questions)
                              if j != i]
                    if others and any(k in packed
                                      for k in rng.choice(others)):
                        shuf += 1
            shuf /= len(seeds)
            n = len(questions)
            totals[budget][0] += real
            totals[budget][1] += shuf
            totals[budget][2] += n
            print("%-16s %7d %6d/%-3d %8.1f/%-3d"
                  % (label, budget, real, n, shuf, n))

    print()
    for budget in BUDGETS:
        real, shuf, n = totals[budget]
        print("  pooled @%d   real %.0f%%   shuffled %.0f%%   ratio %.1fx"
              % (budget, 100.0 * real / n, 100.0 * shuf / n,
                 real / max(0.5, shuf)))
    return totals


def turn_seeds(logs, seeds):
    """Re-run the turn shuffle at several seeds."""
    print("\n" + "=" * 68)
    print("CONTROL 2  the turn shuffle, across seeds")
    print("=" * 68 + "\n")
    rows = []
    for seed in seeds:
        out = subprocess.run(
            [sys.executable, str(HERE / "audit_needed.py"), logs,
             "--seed", str(seed)],
            capture_output=True, text=True, timeout=3600)
        real = shuf = cross = None
        for line in out.stdout.splitlines():
            # startswith, not `in`: the summary also prints "marked by BOTH
            # the real and the shuffled reply", which matches loosely and
            # parses to the word BOTH.
            stripped = line.strip()
            if stripped.startswith("real reply"):
                real = int(stripped.split()[2])
            elif stripped.startswith("shuffled reply"):
                shuf = int(stripped.split()[2])
            elif stripped.startswith("cross-log reply"):
                cross = int(stripped.split()[2])
        if real:
            rows.append((seed, real, shuf, cross))
            print("  seed %-3d  real %5d   shuffled %5d (%3.0f%%)   "
                  "cross-log %5d (%3.0f%%)"
                  % (seed, real, shuf, 100.0 * shuf / real,
                     cross, 100.0 * cross / real))
    if rows:
        s = [100.0 * r[2] / r[1] for r in rows]
        c = [100.0 * r[3] / r[1] for r in rows]
        print("\n  shuffled  mean %.0f%%  (min %.0f, max %.0f)"
              % (statistics.mean(s), min(s), max(s)))
        print("  cross-log mean %.0f%%  (min %.0f, max %.0f)"
              % (statistics.mean(c), min(c), max(c)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default="/sessions/admiring-sharp-keller/mnt/RP_Logs")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--skip-turns", action="store_true")
    args = ap.parse_args()

    seeds = list(range(1, args.seeds + 1))
    chat = chat_shuffle(seeds)
    rows = [] if args.skip_turns else turn_seeds(args.logs, seeds)

    print("\n" + "=" * 68)
    real, shuf, n = chat[300]
    ratio = real / max(0.5, shuf)
    print("VERDICT")
    print("=" * 68)
    if ratio < 1.5:
        print("  The shuffle cannot separate real from wrong even on a hand-\n"
              "  written answer key. The instrument is broken and the turn\n"
              "  verdict is VOID -- the benchmark is not cleared, it is\n"
              "  simply untested.")
    else:
        print("  The shuffle separates real from wrong by %.1fx on a hand-\n"
              "  written key, so it is a working instrument." % ratio)
        if rows:
            mean = statistics.mean([100.0 * r[2] / r[1] for r in rows])
            print("  On the turn benchmark it separates them by %.2fx.\n"
                  "  The turn metric does not measure dependence."
                  % (100.0 / mean))
    return 0


if __name__ == "__main__":
    sys.exit(main())
