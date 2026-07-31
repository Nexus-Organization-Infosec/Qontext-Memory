#!/usr/bin/env python3
"""
Seed a vocabulary weave from a corpus, once, so it starts life useful.

A weave built from a person's own chat logs is too sparse to help — measured:
30,000 tokens buys about a thousand woven words, and retrieval gets slightly
worse rather than better. Seeding fixes that. The result is still no
dependency at runtime: a `.qw` file is JSON, word -> neighbours, not a model.

    python seed_weave.py CORPUS_DIR -o weave.qw
    python seed_weave.py CORPUS_DIR -o weave.qw --vocab 40000 --limit-mb 400
    python seed_weave.py CORPUS_DIR -o weave.qw --add-logs /path/to/chats

Reads .txt, .xml and .bz2 (Wikipedia dumps), streaming, with two passes:

  1. count words, keep the most frequent `--vocab` of them
  2. count co-occurrence pairs among those only, pruning rare pairs as it
     goes so memory stays bounded regardless of corpus size

Then prunes to the strongest threads per word and writes the weave.

Where to get a corpus (any of these work, smallest first):
  * WikiText-2      ~2M tokens    — enough to test the pipeline
  * WikiText-103    ~103M tokens  — plenty; plain text, no markup
  * simplewiki dump ~250MB bz2    — simple English, markup stripped below
  * Project Gutenberg plain-text books — good prose, easy to concatenate
"""

import argparse
import bz2
import re
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from qontext_weave import WordWeave, _NOISE, MIN_WORD  # noqa: E402

PRUNE_EVERY = 200_000          # sentences between memory sweeps
KEEP_PAIR_AFTER_PRUNE = 2      # pairs seen fewer times than this are dropped
WINDOW = 8                     # tighter than the default: precision over reach

_WIKI_MARKUP = [
    (re.compile(r"<ref[^>]*>.*?</ref>", re.S), " "),
    (re.compile(r"<[^>]+>"), " "),
    (re.compile(r"\{\{[^}]*\}\}"), " "),
    (re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]"), r"\1"),
    (re.compile(r"https?://\S+"), " "),
    (re.compile(r"[=*#|]+"), " "),
    (re.compile(r"&[a-z]+;"), " "),
]
_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z'-]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def clean(line):
    for pattern, replacement in _WIKI_MARKUP:
        line = pattern.sub(replacement, line)
    return line


def lines(paths, limit_bytes):
    """Stream every line of every file, stopping at the byte limit."""
    seen = 0
    for path in paths:
        opener = bz2.open if path.suffix == ".bz2" else open
        try:
            with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    seen += len(line)
                    if limit_bytes and seen > limit_bytes:
                        return
                    yield line
        except OSError as error:
            print("  skipped %s (%s)" % (path.name, error))


def tokens_of(sentence, vocabulary=None):
    out = []
    for match in _TOKEN.finditer(sentence):
        word = match.group(0).lower().strip("'-")
        if len(word) < MIN_WORD or word in _NOISE:
            continue
        if vocabulary is None or word in vocabulary:
            out.append(word)
    return out


def gather(corpus_dir):
    paths = []
    for pattern in ("*.txt", "*.xml", "*.bz2", "*.raw", "*.tokens"):
        paths.extend(sorted(Path(corpus_dir).rglob(pattern)))
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="directory of corpus files")
    ap.add_argument("-o", "--out", default="weave.qw")
    ap.add_argument("--vocab", type=int, default=40000,
                    help="how many of the most frequent words to keep")
    ap.add_argument("--limit-mb", type=int, default=0,
                    help="stop after this many megabytes (0 = no limit)")
    ap.add_argument("--neighbours", type=int, default=12)
    ap.add_argument("--prune-every", type=int, default=PRUNE_EVERY,
                    help="sentences between memory sweeps; lower if RAM is tight")
    ap.add_argument("--add-logs", help="also learn from a directory of chats")
    args = ap.parse_args()

    paths = gather(args.corpus)
    if not paths:
        print("no corpus files found in %s" % args.corpus)
        return 1
    limit = args.limit_mb * 1024 * 1024
    print("corpus: %d files%s" % (len(paths),
                                  ", capped at %d MB" % args.limit_mb
                                  if limit else ""))

    # ---- pass 1: which words are worth counting pairs for
    start = time.time()
    unigrams = Counter()
    total = 0
    for line in lines(paths, limit):
        words = tokens_of(clean(line))
        unigrams.update(words)
        total += len(words)
        if len(unigrams) > args.vocab * 8:
            for word, count in list(unigrams.items()):
                if count < 2:
                    del unigrams[word]
    vocabulary = {w for w, _ in unigrams.most_common(args.vocab)}
    print("pass 1: %d tokens, vocabulary %d (%.0fs)"
          % (total, len(vocabulary), time.time() - start))

    # ---- pass 2: co-occurrence among those words only
    weave = WordWeave()
    weave._unigrams = Counter({w: unigrams[w] for w in vocabulary})
    weave._total = total
    pairs = weave._pairs
    sentences = 0
    for line in lines(paths, limit):
        for sentence in _SENTENCE.split(clean(line)):
            words = tokens_of(sentence, vocabulary)
            if len(words) < 2:
                continue
            sentences += 1
            for i, left in enumerate(words):
                for right in words[i + 1:i + 1 + WINDOW]:
                    if left != right:
                        pairs[(left, right) if left < right
                              else (right, left)] += 1
            if sentences % args.prune_every == 0:
                before = len(pairs)
                for pair, count in list(pairs.items()):
                    if count < KEEP_PAIR_AFTER_PRUNE:
                        del pairs[pair]
                print("   %d sentences, pairs %d -> %d"
                      % (sentences, before, len(pairs)))
    print("pass 2: %d sentences, %d pairs kept (%.0fs)"
          % (sentences, len(pairs), time.time() - start))

    if args.add_logs:
        from rp_probe import load
        chats = gather(args.add_logs)
        for path in chats:
            try:
                for _, _, text in load(path):
                    weave.learn(text)
            except Exception:
                continue
        print("thickened with %d chat files" % len(chats))

    weave.prune(args.neighbours)
    weave.save(args.out)
    size = Path(args.out).stat().st_size
    print("\nwoven %d words -> %s (%.1f MB)"
          % (len(weave), args.out, size / 1024 / 1024))
    for probe in ("cancel", "class", "husband", "vampire", "invoice", "allergic"):
        rows = weave.related(probe, limit=6)
        print("  %-10s -> %s" % (probe, ", ".join("%s %.2f" % r for r in rows)
                                 or "(nothing)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
