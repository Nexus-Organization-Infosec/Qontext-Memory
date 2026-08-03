#!/usr/bin/env python3
"""Is a "needed" fact actually needed?

Every turn-shaped number in this project rests on one inference:

    a knot shares a rare word with the next reply
        => the reply drew on that knot

That is a proxy, and it has an obvious way to be wrong. The replies in these
logs were generated from the *full transcript*, not from a pack, so their
vocabulary reflects the whole conversation. Any knot sharing a rare word gets
marked needed whether or not it was load-bearing. If most of the 1,289 needed
facts are coincidence, then 4.3% and 16.6% are both measuring nothing and the
choice between them was never real.

Three controls, cheapest first.

1. SHUFFLE. Score turn i's memory against a *different* turn's reply from the
   same log. Domain, characters and register all still match -- only the
   pairing is wrong. A metric measuring dependence should collapse. A metric
   measuring vocabulary overlap will not notice.

2. CROSS-LOG. The same, with a reply from a different conversation entirely.
   This is the floor: whatever survives here is pure lexical coincidence.

3. PERSISTENCE. How often is the same knot marked needed at many different
   turns? A fact the reply genuinely reached for should be needed at a few
   turns. A knot that is "needed" at thirty different turns is just a knot
   containing a word the log says a lot.

Plus a printed sample, because a number cannot tell you whether
"Kestrel" linking a reply to a knot about Kestrel is evidence or tautology.

    python audit_needed.py /path/to/RP_Logs
"""

import argparse
import random
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import (content_words, needed_knots,         # noqa: E402
                          RECENCY_WINDOW, EXCLUDED, MAX_DOC_COUNT)


def scored_turns(turns):
    """(index, user_text, reply) for every turn the benchmark would score."""
    out = []
    for i, (_s, is_user, text) in enumerate(turns):
        if is_user and i + 1 < len(turns):
            out.append((i, text, turns[i + 1][2]))
    return out


def evidence(mem, reply, free):
    """The (rare word, knot) pairs behind a needed set -- the metric's reasons."""
    reply_words = content_words(reply) - free
    knots = mem.entries()
    frequency = Counter()
    tokenised = []
    for knot in knots:
        words = content_words(knot)
        tokenised.append((knot, words))
        frequency.update(words)
    out = []
    for knot, words in tokenised:
        shared = [w for w in words & reply_words
                  if frequency[w] <= MAX_DOC_COUNT]
        if shared:
            out.append((min(frequency[w] for w in shared),
                        min(shared, key=lambda w: frequency[w]), knot))
    out.sort()
    return out[:5]


def run(path, other_replies, rng, samples):
    turns = load(path)
    mem = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
    scored = scored_turns(turns)
    replies = [r for _i, _t, r in scored]

    real = shuffled = crossed = 0
    both = 0
    marked = Counter()
    turn_count = 0

    for i, (_speaker, is_user, text) in enumerate(turns):
        reply = turns[i + 1][2] if i + 1 < len(turns) else None
        if is_user and reply and len(mem):
            free = set()
            for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                free |= content_words(earlier)

            need = needed_knots(mem, reply, free)
            if need:
                turn_count += 1
                real += len(need)
                marked.update(need)

                wrong = rng.choice([r for r in replies if r is not reply]
                                   or [reply])
                sneed = needed_knots(mem, wrong, free)
                shuffled += len(sneed)
                both += len(need & sneed)

                if other_replies:
                    crossed += len(needed_knots(
                        mem, rng.choice(other_replies), free))

                # Link words only. An earlier version of this printed the
                # knot and the reply verbatim, which put private log content
                # on screen to make a point that the word alone already
                # makes: if the "distinctive" evidence is `already` or
                # `feel`, the metric is not finding facts.
                if len(samples) < 25 and rng.random() < 0.08:
                    for _rank, word, _knot in evidence(mem, reply, free)[:1]:
                        samples.append(word)
        mem.observe("user" if is_user else _speaker, text)

    repeats = [n for n in marked.values()]
    return {
        "name": path.name, "turns": turn_count, "real": real,
        "shuffled": shuffled, "crossed": crossed, "both": both,
        "distinct": len(marked),
        "max_repeat": max(repeats) if repeats else 0,
        "mean_repeat": statistics.mean(repeats) if repeats else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    paths = [p for p in sorted(Path(args.logs).glob("*.txt"))
             if p.name not in EXCLUDED]

    # a pool of replies from other conversations, for the cross-log floor
    pool = {}
    for path in paths:
        pool[path.name] = [r for _i, _t, r in scored_turns(load(path))]

    rows, samples = [], []
    for path in paths:
        others = [r for name, rs in pool.items() if name != path.name
                  for r in rs]
        row = run(path, others, rng, samples)
        if row["real"]:
            rows.append(row)

    print("%-12s %6s %9s %9s %9s %9s"
          % ("log", "real", "shuffled", "cross-log", "overlap", "repeat"))
    for r in rows:
        print("%-12s %6d %8d %9d %8d%% %9.1f"
              % (r["name"], r["real"], r["shuffled"], r["crossed"],
                 round(100.0 * r["both"] / r["real"]), r["mean_repeat"]))

    real = sum(r["real"] for r in rows)
    shuf = sum(r["shuffled"] for r in rows)
    cross = sum(r["crossed"] for r in rows)
    both = sum(r["both"] for r in rows)
    print("\npooled over %d logs, %d scored turns"
          % (len(rows), sum(r["turns"] for r in rows)))
    print("  real reply           %5d needed facts   (100%%)" % real)
    print("  shuffled reply       %5d               (%4.0f%% of real)"
          % (shuf, 100.0 * shuf / real))
    print("  cross-log reply      %5d               (%4.0f%% of real)"
          % (cross, 100.0 * cross / real))
    print("  marked by BOTH the real and the shuffled reply: %.0f%%"
          % (100.0 * both / real))
    print("  a knot is marked needed at %.1f different turns on average, "
          "%d at most"
          % (statistics.mean([r["mean_repeat"] for r in rows]),
             max(r["max_repeat"] for r in rows)))

    print("\nthe words the metric treated as distinctive evidence:")
    print("  %s" % ", ".join(sorted(set(samples))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
