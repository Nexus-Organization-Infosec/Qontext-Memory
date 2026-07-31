#!/usr/bin/env python3
"""Build dailydialog_pairs.tsv — real filler for long_bench.

Every benchmark conversation in this project was synthetic, which is the
largest external-validity gap in the report. This turns DailyDialog into
(utterance, reply) pairs so the conversation surrounding the planted facts is
human-written and keeps real conversational adjacency.

Exchanges containing any of our answer keywords are dropped. "Friday" occurs
226 times in the raw corpus and "short" 197; left in, a transcript could score
a correct answer by coincidence and the scorer could never tell.

    pip install pyarrow
    python build_filler.py /path/to/dailydialog   # dir with the .parquet files
"""
import argparse
import random
import re
from pathlib import Path

KEYWORDS = ["marta", "utrecht", "nurse", "rust", "friday", "heron", "bikkel",
            "brief", "short", "march", "trello", "demo", "sprint", "codename",
            "dog", "deadline"]
BAD = re.compile(r"\b(%s)\b" % "|".join(KEYWORDS), re.I)
CELL = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")


def utterances(cell):
    out = []
    for first, second in CELL.findall(cell.strip()):
        text = (first or second).replace("\\'", "'").replace('\\"', '"')
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        if text:
            out.append(text)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="directory holding the DailyDialog parquet files")
    ap.add_argument("-o", "--out", default="dailydialog_pairs.tsv")
    ap.add_argument("--limit", type=int, default=30000)
    args = ap.parse_args()

    import pyarrow.parquet as pq
    pairs, dropped = [], 0
    for path in sorted(Path(args.corpus).glob("*.parquet")):
        table = pq.ParquetFile(path).read(columns=["dialog"])
        for cell in table.column("dialog").to_pylist():
            turns = utterances(cell)
            for i in range(0, len(turns) - 1, 2):
                said, replied = turns[i], turns[i + 1]
                if len(said) < 15 or len(replied) < 10:
                    continue
                if BAD.search(said) or BAD.search(replied):
                    dropped += 1
                    continue
                if "\t" in said or "\t" in replied:
                    continue
                pairs.append((said, replied))

    random.Random(1).shuffle(pairs)
    pairs = pairs[:args.limit]
    Path(args.out).write_text("\n".join("%s\t%s" % p for p in pairs),
                              encoding="utf-8")
    print("kept %d exchanges, dropped %d for answer-keyword collisions"
          % (len(pairs), dropped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
