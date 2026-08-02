#!/usr/bin/env python3
"""Does an embedding retriever clear the bar the weave failed to?

The decomposition says 80% of missed facts are unreachable — zero lexical
score, never candidates. A semantic bridge is judged on exactly one number:

    of the unreachable facts, how many does it make reachable,
    and at what cost in candidate-pool inflation?

The WikiText-103 co-occurrence weave scores 6.8% on that measure. This runs
the same protocol with sentence embeddings, which are trained to preserve
contextual similarity rather than to count co-occurrence — the difference that
might or might not bridge "didn't she cancel that?" to "didn't you have class
though?".

A bridge proposes K entry points per query, so K is also its cost: the pool
grows by K whether or not any of them is right. Reporting reach at several K
keeps the trade visible instead of tuning it away.

    pip install model2vec
    python embed_bridge.py

Note what this deliberately does NOT do: touch supersession. "Alice is at the
tavern" and "Bob is at the tavern" are near-identical in any embedding space
and must never merge. Retrieval only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import qontext_memory as qm                                    # noqa: E402
from rp_probe import load                                      # noqa: E402
from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW  # noqa: E402

# A static distilled model: no torch, no context window, essentially a
# well-trained bag of word vectors. That makes this a LOWER BOUND on what
# embeddings can do here — a true contextual sentence encoder should score
# higher. If even the lower bound clears the weave's 6.8%, the bridge category
# is worth engineering.
MODEL = "minishlab/potion-base-8M"
TOPK = (5, 10, 20, 50)
LOGS = ("log13.txt", "log1.txt", "log5.txt", "log2.txt", "log6.txt")
ROOT = Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")


def main():
    from model2vec import StaticModel
    import numpy as np

    model = StaticModel.from_pretrained(MODEL)
    cache = {}

    def embed(texts):
        fresh = [t for t in texts if t not in cache]
        if fresh:
            vectors = model.encode(fresh)
            vectors = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True)
                                 + 1e-9)
            for text, vector in zip(fresh, vectors):
                cache[text] = vector
        return np.stack([cache[t] for t in texts])

    unreachable = 0
    reached = {k: 0 for k in TOPK}
    turns_counted = 0

    for name in LOGS:
        turns = load(ROOT / name)
        mem = qm.QontextMemory(max_entries=10 ** 6, speakers="all")
        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(mem):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(mem, reply, free)
                if needed:
                    lexical = {r["text"] for _s, r in mem._ranked(text)}
                    missing = [k for k in needed if k not in lexical]
                    if missing:
                        turns_counted += 1
                        knots = mem.entries()
                        matrix = embed(knots)
                        query = embed([text])[0]
                        scores = matrix @ query
                        order = np.argsort(-scores)
                        for k in TOPK:
                            top = {knots[j] for j in order[:k]}
                            reached[k] += len(set(missing) & top)
                        unreachable += len(missing)
            mem.observe("user" if is_user else speaker, text)

    print("\nunreachable needed facts: %d   (across %d turns)"
          % (unreachable, turns_counted))
    print("\n  bridge            reached      cost (extra candidates/turn)")
    print("  %-16s %4d (%4.1f%%)   %s"
          % ("weave, ~6 terms", 31, 100.0 * 31 / max(1, unreachable), "+9"))
    for k in TOPK:
        print("  %-16s %4d (%4.1f%%)   +%d"
              % ("embeddings top-%d" % k, reached[k],
                 100.0 * reached[k] / max(1, unreachable), k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
