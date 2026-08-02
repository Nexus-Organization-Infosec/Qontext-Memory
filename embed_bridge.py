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

    pip install model2vec                       # static, no torch
    python embed_bridge.py

    pip install sentence-transformers           # contextual, needs torch
    python embed_bridge.py --model sentence-transformers/all-MiniLM-L6-v2

The backend is chosen from the model name unless --backend says otherwise, and
the log directory is found automatically if it sits somewhere obvious.

Note what this deliberately does NOT do: touch supersession. "Alice is at the
tavern" and "Bob is at the tavern" are near-identical in any embedding space
and must never merge. Retrieval only.
"""
import argparse
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

# Excluded from every roleplay measurement after a content screen.
EXCLUDED = {"log8.txt", "log12.txt"}

# Where the logs might be. Tried in order; first hit wins.
CANDIDATE_ROOTS = (
    Path(r"C:\Users\hylke\Documents\RP_Logs"),
    Path.home() / "Documents" / "RP_Logs",
    Path("/sessions/admiring-sharp-keller/mnt/RP_Logs"),
    Path(__file__).resolve().parent.parent / "RP_Logs",
)


def find_logs(given):
    if given:
        return Path(given)
    for root in CANDIDATE_ROOTS:
        if root.is_dir():
            return root
    raise SystemExit("Could not find the log directory. Pass --logs PATH.")


def encoder(name, backend):
    """Returns embed(texts) -> normalised matrix, for either backend.

    Static models (model2vec) need no torch and have no context window, so
    they are a lower bound. A sentence-transformer is the real test.
    """
    import numpy as np

    if backend == "auto":
        backend = "static" if name.startswith("minishlab/") else "sentence"

    if backend == "static":
        from model2vec import StaticModel
        model = StaticModel.from_pretrained(name)

        def raw(texts):
            return model.encode(texts)
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(name)

        def raw(texts):
            return model.encode(texts, batch_size=128,
                                show_progress_bar=False)

    cache = {}

    def embed(texts):
        fresh = [t for t in texts if t not in cache]
        if fresh:
            vectors = np.asarray(raw(fresh), dtype="float32")
            vectors = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True)
                                 + 1e-9)
            for text, vector in zip(fresh, vectors):
                cache[text] = vector
        return np.stack([cache[t] for t in texts])

    return embed, backend


def main():
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--backend", choices=("auto", "static", "sentence"),
                    default="auto")
    ap.add_argument("--logs", help="directory of roleplay logs")
    ap.add_argument("--topk", default=",".join(str(k) for k in TOPK))
    args = ap.parse_args()

    root = find_logs(args.logs)
    topk = tuple(int(k) for k in args.topk.split(",") if k.strip())
    embed, backend = encoder(args.model, args.backend)
    print("logs:    %s" % root)
    print("model:   %s  (%s backend)" % (args.model, backend))

    names = [n for n in LOGS if n not in EXCLUDED
             and (root / n).is_file()]
    if not names:
        raise SystemExit("No usable logs in %s" % root)

    unreachable = 0
    reached = {k: 0 for k in topk}
    turns_counted = 0

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
                    lexical = {r["text"] for _s, r in mem._ranked(text)}
                    missing = [k for k in needed if k not in lexical]
                    if missing:
                        turns_counted += 1
                        knots = mem.entries()
                        matrix = embed(knots)
                        query = embed([text])[0]
                        scores = matrix @ query
                        order = np.argsort(-scores)
                        for k in topk:
                            top = {knots[j] for j in order[:k]}
                            reached[k] += len(set(missing) & top)
                        unreachable += len(missing)
            mem.observe("user" if is_user else speaker, text)

    print("\nunreachable needed facts: %d   (across %d turns)"
          % (unreachable, turns_counted))
    print("\n  bridge            reached      cost (extra candidates/turn)")
    print("  %-16s %4d (%4.1f%%)   %s"
          % ("weave, ~6 terms", 31, 100.0 * 31 / max(1, unreachable), "+9"))
    for k in topk:
        print("  %-16s %4d (%4.1f%%)   +%d"
              % ("embeddings top-%d" % k, reached[k],
                 100.0 * reached[k] / max(1, unreachable), k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
