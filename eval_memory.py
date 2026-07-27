#!/usr/bin/env python3
"""
Offline evaluation for qontext_memory.QuipuMemory — no model server needed.

A fact counts as captured when it appears in pack(question, budget); this is
the same containment criterion benchmark.py uses in --mock mode, and it is an
upper bound on what a model could answer from the pack.

Reports, per conversation:
  recall   facts found in the pack, at each budget
  density  stored chars / observed chars (lower is denser memory)
  noise    share of packed entries that match no question keyword

Usage:
    python eval_memory.py                 # evaluate the current module
    python eval_memory.py --against FILE  # compare a second module against it
"""

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import conversations as cv  # noqa: E402

SUITES = [
    ("A  (tuned-on)", cv.CONV_A, cv.QUESTIONS_A),
    ("B  (held-out)", cv.CONV_B, cv.QUESTIONS_B),
    ("C  (stress)  ", cv.CONV_C, cv.QUESTIONS_C),
]
BUDGETS = (150, 300, 800)


def load(path):
    spec = importlib.util.spec_from_file_location("qm_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.QuipuMemory


def build(QM, conv):
    m = QM()
    for speaker, text in conv:
        m.observe(speaker, text)
    return m


def evaluate(QM):
    """Returns {suite: {'density':d, 'entries':n, budgets:{b:(hits,total,misses,noise)}}}"""
    out = {}
    for label, conv, questions in SUITES:
        m = build(QM, conv)
        st = m.stats()
        rec = {
            "density": st["stored_chars"] / max(1, st["observed_chars"]),
            "entries": len(m.entries()),
            "budgets": {},
        }
        all_keywords = [k for _, kws in questions for k in kws]
        for budget in BUDGETS:
            hits, misses, packed, useful = 0, [], 0, 0
            for q, kws in questions:
                p = m.pack(q, budget)
                assert len(p) <= budget, "pack over budget: %d" % len(p)
                if any(k in p.lower() for k in kws):
                    hits += 1
                else:
                    misses.append(q)
                for line in p.split("\n"):
                    if not line.strip():
                        continue
                    packed += 1
                    if any(k in line.lower() for k in all_keywords):
                        useful += 1
            noise = 1.0 - (useful / packed) if packed else 0.0
            rec["budgets"][budget] = (hits, len(questions), misses, noise)
        out[label] = rec
    return out


def show(name, res, baseline=None):
    print("== %s" % name)
    print("%-14s %-9s %-7s %s" % ("suite", "density", "entries", "  ".join(
        "recall@%d  noise@%d" % (b, b) for b in BUDGETS)))
    for label, rec in res.items():
        cells = []
        for b in BUDGETS:
            hits, total, _, noise = rec["budgets"][b]
            delta = ""
            if baseline:
                bh = baseline[label]["budgets"][b][0]
                if hits != bh:
                    delta = " (%+d)" % (hits - bh)
            cells.append("%2d/%-2d%-6s %4.0f%%   " % (hits, total, delta, noise * 100))
        print("%-14s %-9.2f %-7d %s" % (label, rec["density"], rec["entries"],
                                        "".join(cells)))
    for label, rec in res.items():
        misses = rec["budgets"][BUDGETS[-1]][2]
        if misses:
            print("   %s misses @%d: %s" % (label.strip(), BUDGETS[-1],
                                            "; ".join(misses)))
    print()


def perf(QM, sizes=(100, 1000, 5000)):
    """pack() latency as the memory grows, plus the cost of one add().

    The sizes deliberately exceed the default 500-knot cap, so this is a
    worst case: an uncapped memory where every knot has to be considered as
    a possible near-duplicate of the incoming one.
    """
    import time
    print("== latency (uncapped memory — worst case)")
    print("%-8s %-12s %-12s %s"
          % ("knots", "build", "per add", "pack (median of 50)"))
    for n in sizes:
        m = QM(max_entries=n + 10)
        t0 = time.perf_counter()
        for i in range(n):
            m.add("the user's %s number %d is Rust and Postgres in Utrecht"
                  % ("lucky" if i % 2 else "unlucky", i))
        build = time.perf_counter() - t0
        samples = []
        for i in range(50):
            t0 = time.perf_counter()
            m.pack("Which programming language does the user use?", 300)
            samples.append(time.perf_counter() - t0)
        samples.sort()
        print("%-8d %-12s %-12s %.2f ms"
              % (n, "%.2f s" % build, "%.2f ms" % (1000.0 * build / max(1, n)),
                 samples[len(samples) // 2] * 1000))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", default=str(HERE / "qontext_memory.py"))
    ap.add_argument("--against", help="second module to compare (the baseline)")
    ap.add_argument("--perf", action="store_true", help="also time pack()")
    args = ap.parse_args()

    baseline = None
    if args.against:
        baseline = evaluate(load(args.against))
        show("baseline: %s" % args.against, baseline)
    QM = load(args.module)
    res = evaluate(QM)
    show("current: %s" % args.module, res, baseline)
    if args.perf:
        perf(QM)


if __name__ == "__main__":
    main()
