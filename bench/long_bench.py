#!/usr/bin/env python3
"""
Does the saving hold up over a long conversation?

The five-condition benchmark measures a 26-turn chat, where a full transcript
costs 476 tokens and a Qontext pack costs 69. Seven times cheaper, and seven
times almost nothing. The claim worth testing is different: a transcript grows
with every turn while a pack does not, so the interesting quantity is the
*shape of both curves* as the conversation gets long.

This plants ten facts in the opening turns, buries them under hundreds of
turns of unrelated chatter, and then asks about them — the honest test of a
memory, since the answers are far outside any recency window. At each length
it records, for both conditions:

    accuracy          out of the questions asked
    prompt tokens     what the model had to read
    prompt time       what that cost in seconds, from the server's own timings

    python long_bench.py                      # 26, 50, 100, 200 turns
    python long_bench.py --turns 26,100,400,800
    python long_bench.py --questions 5        # fewer calls, faster
    python long_bench.py --no-full            # skip the expensive condition

A single run of a single seed is one draw from a distribution, and the
distractor cells vary a lot between draws. `--seeds 7,11,23,42,101` builds a
different conversation from each seed, runs the whole grid on every one, and
reports mean and range per cell — which is the difference between "the
transcript scored 5/10" and "the transcript scores 3 to 6 out of 10".

Note the full-transcript condition gets slow and eventually impossible: at
several hundred turns it exceeds an 8K window, which is the finding rather
than a failure. Start the server with a large -c to see how far it goes, or
let it fail and watch where.
"""

import argparse
import json
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qontext_memory import QontextMemory  # noqa: E402

API_URL = "http://127.0.0.1:8080/v1/chat/completions"
PACK_BUDGET = 300

# A reasoning model spends its first several hundred tokens thinking, and a
# crowded context makes it think *longer*. Measured against a full 800-turn
# transcript: up to 7,089 characters of reasoning — roughly 1,800 tokens —
# before the first character of an answer. Any cap below that truncates
# mid-thought and returns nothing, which scores as a miss and is not one.
#
# This has now cost the project three separate rounds of wrong conclusions
# (100, then 512, then 1536). The cap is deliberately far above the observed
# worst case, `thinking_per_call` is measured so we can see it approaching,
# and empty answers are their own category rather than folded into "wrong".
MAX_TOKENS = 4096
SYSTEM = ("Answer the question using only the provided context. Answer in one "
          "short sentence. If the context does not contain the answer, say: "
          "unknown.")

# Ten facts, stated in the first turns and never mentioned again.
FACTS = [
    ("People call me Marta.", "What is the user's name?", ["marta"]),
    ("I'm based in Utrecht, near the old canal.", "Where does the user live?",
     ["utrecht"]),
    ("I work as a nurse, mostly night shifts.", "What is the user's job?",
     ["nurse"]),
    ("Lately I code in Rust for fun.",
     "Which programming language does the user use?", ["rust"]),
    ("The sprint demo is on Friday at 10:00.", "When is the sprint demo?",
     ["friday"]),
    ("Our project is codenamed heron-nest.", "What is the project codenamed?",
     ["heron"]),
    ("My dog is called Bikkel.", "What is the dog's name?", ["bikkel"]),
    ("Please keep explanations brief, I skim a lot.",
     "How should explanations be written?", ["brief", "short"]),
    ("The report is due March 3rd, hard deadline.", "When is the report due?",
     ["march"]),
    ("We track tasks in Trello.", "Where are tasks tracked?", ["trello"]),
]

# Filler that carries no answer and does not repeat itself — repetition would
# be collapsed by supersession and would understate the transcript's growth.
SUBJECTS = ["the weather", "the tram", "my coffee", "the neighbours", "that film",
            "the football", "this rain", "the new bakery", "my headphones",
            "the traffic", "that podcast", "the garden", "the radio"]
VERBS = ["was strange", "kept me up", "went cold", "made no sense",
         "turned out fine", "was a disaster", "surprised me", "dragged on",
         "was worth it", "fell over", "sounded odd", "cheered me up"]
TAILS = ["again", "this morning", "for once", "somehow", "as usual",
         "of all things", "which was odd", "but never mind", "apparently",
         "if you can believe it"]
REPLIES = ["Understood.", "Noted.", "That happens.", "Fair enough.",
           "I see.", "Right.", "Makes sense.", "Ha.", "Good to know.",
           "Mm."]

# Decoys: facts of the *same shape* as the planted ones, about other people
# and other things. Chatter about trams can never be mistaken for "my dog is
# called Bikkel"; "my neighbour's dog is called Rex" can. This is the honest
# test, because a long real conversation is full of near-misses.
DECOY_NAMES = ["Rex", "Sanne", "Joris", "Nadia", "Bram", "Elif", "Tomas",
               "Wietse", "Fenna", "Ruben", "Iris", "Kasper", "Noor", "Sem"]
DECOY_PLACES = ["Groningen", "Antwerp", "Leiden", "Bruges", "Delft", "Ghent",
                "Nijmegen", "Haarlem", "Aachen", "Zwolle"]
DECOY_JOBS = ["a baker", "an electrician", "a teacher", "a paramedic",
              "a carpenter", "a translator", "a florist", "a mechanic"]
DECOY_LANGS = ["Go", "Elixir", "Kotlin", "Clojure", "Zig", "Scala", "OCaml"]
DECOY_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Saturday"]
DECOY_TOOLS = ["Jira", "Asana", "Linear", "Notion", "Basecamp", "Height"]
DECOY_PROJECTS = ["otter-lamp", "willow-basket", "amber-lattice", "fox-signal",
                  "cedar-post", "iron-kite"]
DECOY_MONTHS = ["January", "April", "June", "September", "November"]

DECOY_TEMPLATES = [
    "My neighbour's dog is called {name}.",
    "{name} lives in {place} now.",
    "{name} works as {job}.",
    "{name} codes in {lang} at work.",
    "{name}'s standup is on {day}.",
    "The other team's project is codenamed {project}.",
    "{name} tracks their tasks in {tool}.",
    "{name}'s report is due {month} 12th.",
    "{name} asked for longer explanations, oddly.",
    "{name} moved the review to {day} afternoon.",
]


# Real filler, so the conversation around the planted facts is not our own
# generator talking to itself.
#
# Every benchmark conversation in this project was synthetic: templated chatter
# ("the tram was strange again, number 42") that no memory could mistake for an
# answer and no model could find interesting. That is the largest external
# validity gap in the report.
#
# dailydialog_pairs.tsv holds 30,000 consecutive human-written exchanges from
# DailyDialog, kept as (utterance, reply) pairs so real conversational
# adjacency survives. 550 exchanges were dropped because they contained one of
# our answer keywords — with "Friday" appearing 226 times in the raw corpus, a
# transcript could otherwise score a correct answer by coincidence.
#
# What this does NOT fix: the planted facts and the questions are still ours.
# It makes the environment real, not the probe.
# Splicing unrelated dialogues under one speaker has a side effect that is an
# artifact of the construction rather than a property of real conversation:
# 13.6% of exchanges make a first-person identity claim, and the extractor
# faithfully turns them into knots about *the user* — "the user is Monica",
# "the user is Greg Wu, Head of Consultancy", "the user is from the plains of
# the Midwest". Those do not compete with the planted facts, they contradict
# them, and no real single conversation has one speaker claiming three names.
#
# `daily-clean` drops those exchanges, which separates "real conversation is
# harder" from "our splice injects contradictions". Both are worth measuring;
# only the first is a finding.
IDENTITY_CLAIM = re.compile(
    r"\b(?:i am|i'm|my name is|i work|i live|i'm called|call me)\b", re.I)
DAILY = Path(__file__).resolve().parent / "dailydialog_pairs.tsv"
_daily_cache = {}


def daily_pairs(clean=False):
    if clean not in _daily_cache:
        if not DAILY.exists():
            raise SystemExit(
                "%s not found — run build_filler.py to create it" % DAILY.name)
        rows = [line.split("\t", 1) for line in
                DAILY.read_text(encoding="utf-8").splitlines() if "\t" in line]
        if clean:
            rows = [r for r in rows
                    if not IDENTITY_CLAIM.search(r[0])
                    and not IDENTITY_CLAIM.search(r[1])]
        _daily_cache[clean] = rows
    return _daily_cache[clean]


def decoy(rnd):
    return rnd.choice(DECOY_TEMPLATES).format(
        name=rnd.choice(DECOY_NAMES), place=rnd.choice(DECOY_PLACES),
        job=rnd.choice(DECOY_JOBS), lang=rnd.choice(DECOY_LANGS),
        day=rnd.choice(DECOY_DAYS), tool=rnd.choice(DECOY_TOOLS),
        project=rnd.choice(DECOY_PROJECTS), month=rnd.choice(DECOY_MONTHS))


def build(turns, seed=7, decoy_rate=0.0, filler="synthetic"):
    """A conversation of `turns` messages: facts first, then filler.

    `decoy_rate` is the share of filler that is a *confusable* fact rather
    than chatter — another person's dog, another team's project, another
    day's standup. At zero the retrieval task is trivially easy, because
    nothing in the memory competes with the answer.
    """
    rnd = random.Random(seed)
    pairs = (daily_pairs(filler == "daily-clean")
             if filler.startswith("daily") else None)
    conversation = []
    for statement, _, _ in FACTS:
        conversation.append(("user", statement))
        conversation.append(("assistant", rnd.choice(REPLIES)))
    while len(conversation) < turns:
        if rnd.random() < decoy_rate:
            conversation.append(("user", decoy(rnd)))
            conversation.append(("assistant", rnd.choice(REPLIES)))
        elif pairs is not None:
            said, replied = rnd.choice(pairs)
            conversation.append(("user", said))
            conversation.append(("assistant", replied))
        else:
            conversation.append(("user", "%s %s %s, number %d."
                                 % (rnd.choice(SUBJECTS), rnd.choice(VERBS),
                                    rnd.choice(TAILS), len(conversation))))
            conversation.append(("assistant", rnd.choice(REPLIES)))
    return conversation[:turns]


def ask(context, question, timeout=600, max_tokens=MAX_TOKENS):
    body = json.dumps({
        "model": "local",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": ("Context:\n%s\n\nQuestion: %s"
                                         % (context, question))
             if context else "Question: %s" % question},
        ],
        "temperature": 0.2, "top_k": 64, "top_p": 0.95,
        "max_tokens": max_tokens,
    }).encode()
    request = urllib.request.Request(API_URL, data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as error:
        detail = ""
        if isinstance(error, urllib.error.HTTPError):
            try:
                detail = error.read().decode("utf-8", "replace")[:160]
            except Exception:
                pass
        return None, 0, 0.0, "%s %s" % (type(error).__name__, detail), 0

    message = data["choices"][0]["message"]
    answer = (message.get("content") or "").strip()
    thinking = message.get("reasoning_content") or ""
    if not answer:
        # An empty answer is not a wrong answer. A context full of near-misses
        # makes the model deliberate, and it can spend its whole generation
        # budget doing so — worth distinguishing from "it chose incorrectly".
        answer = "[EMPTY: finish_reason=%s, %d chars of reasoning]" % (
            data["choices"][0].get("finish_reason"), len(thinking))
    usage = data.get("usage", {})
    timings = data.get("timings", {})
    return (answer, usage.get("prompt_tokens", 0),
            timings.get("prompt_ms", 0.0) / 1000.0, None, len(thinking))


def classify(answer):
    """Why a question was missed. These are three different things.

    empty    the model never finished thinking — an instrument limit
    unknown  it declined, having found nothing it trusted
    wrong    it committed to an answer and the answer was false

    Only the third is a retrieval failure. Averaging them together is how a
    truncated generation budget gets published as a memory result.
    """
    if answer.startswith("[EMPTY:"):
        return "empty"
    if "unknown" in answer.lower():
        return "unknown"
    return "wrong"


def measure(conversation, questions, label, context_for):
    correct = failed = 0
    tokens = seconds = 0
    error = None
    misses = []
    kinds = {"empty": 0, "unknown": 0, "wrong": 0}
    thinking = 0
    for statement, question, keywords in questions:
        context = context_for(question)
        answer, prompt_tokens, prompt_seconds, problem, chars = ask(context,
                                                                   question)
        if problem:
            error = problem
            failed += 1
            continue
        tokens += prompt_tokens
        seconds += prompt_seconds
        thinking += chars
        if any(k in answer.lower() for k in keywords):
            correct += 1
        else:
            kind = classify(answer)
            kinds[kind] += 1
            misses.append((question, answer[:80], kind))
    calls = max(1, len(questions) - failed)
    return {"label": label, "correct": correct, "asked": len(questions),
            "failed": failed, "tokens_per_call": tokens // calls,
            "seconds_per_call": seconds / calls, "error": error,
            # How hard the model had to think. Reasoning tokens are generated,
            # so they cost far more per token than prompt tokens do — and a
            # crowded context inflates them. This is a second, independent
            # cost of context that the prompt-token count hides completely.
            "thinking_per_call": thinking // calls,
            "misses": misses, "kinds": kinds,
            # Every call failed at the transport layer: the condition did not
            # run. Scoring that as 0/10 would be a fabricated result.
            "void": failed == len(questions)}


def one_run(length, seed, decoy_rate, questions, skip_full,
            budget=PACK_BUDGET, skip_pack=False, filler="synthetic"):
    conversation = build(length, seed, decoy_rate, filler)
    transcript = "\n".join("%s: %s" % (who, text) for who, text in conversation)
    memory = QontextMemory()
    for who, text in conversation:
        memory.observe(who, text)

    row = {"turns": length, "seed": seed, "decoys": decoy_rate,
           "knots": len(memory), "transcript_chars": len(transcript),
           "budget": budget}
    if not skip_full:
        row["full"] = measure(conversation, questions, "full",
                              lambda q: transcript)
    if skip_pack:
        row["pack"] = {"label": "pack", "correct": 0,
                       "asked": len(questions), "failed": len(questions),
                       "tokens_per_call": 0, "seconds_per_call": 0.0,
                       "thinking_per_call": 0, "error": "skipped",
                       "misses": [], "kinds": {"empty": 0, "unknown": 0,
                                               "wrong": 0}, "void": True}
    else:
        row["pack"] = measure(conversation, questions, "pack",
                              lambda q: memory.pack(q, budget))
    return row


def cell(entry):
    if not entry:
        return "-"
    if entry["void"]:
        return "VOID"
    return "%d/%d" % (entry["correct"], entry["asked"])


def report(row):
    full = row.get("full")
    print("%-7d %-6d %-7d | %-6s %-6s %-5s %-6s | %-6s %-6s %-5s %-6s"
          % (row["turns"], row["seed"], row["knots"],
             cell(full),
             full["tokens_per_call"] if full else "-",
             ("%.1f" % full["seconds_per_call"]) if full else "-",
             full["thinking_per_call"] if full else "-",
             cell(row["pack"]),
             row["pack"]["tokens_per_call"],
             "%.1f" % row["pack"]["seconds_per_call"],
             row["pack"]["thinking_per_call"]))
    if full and full["error"]:
        print("        full transcript DID NOT RUN: %s" % full["error"][:88])
    for key in ("full", "pack"):
        entry = row.get(key)
        for question, answer, kind in (entry or {}).get("misses", []):
            print("        %-4s %-7s %-40s answered: %r"
                  % (key, kind, question[:40], answer))


def summarise(results):
    """Mean and range per (turns, condition), across whatever seeds ran.

    A single seed is one draw. With several, the spread is the finding: a
    cell whose seeds land on 2, 5 and 6 out of 10 does not support a claim
    stated to one decimal place.
    """
    cells, breakdown, think, voided = {}, {}, {}, set()
    for row in results:
        for key in ("full", "pack"):
            entry = row.get(key)
            if not entry:
                continue
            if entry["void"]:
                if entry.get("error") != "skipped":
                    voided.add((row["turns"], key))
                continue
            at = (row["turns"], row["decoys"], key)
            cells.setdefault(at, []).append(entry["correct"])
            tally = breakdown.setdefault(at, {"empty": 0, "unknown": 0,
                                              "wrong": 0})
            for kind, count in entry["kinds"].items():
                tally[kind] += count
            think.setdefault(at, []).append(entry["thinking_per_call"])
    for turns, key in sorted(voided):
        print("\n!! %s at %d turns did not run on any seed — reported as VOID, "
              "not as zero.\n   Restart llama-server with a larger -c and "
              "re-run before quoting this cell." % (key, turns))
    if not any(len(v) > 1 for v in cells.values()):
        return
    asked = results[0]["pack"]["asked"]
    print("\naccuracy across seeds (out of %d)" % asked)
    print("%-6s %-7s %-6s %-4s %-6s %-6s %-14s %-7s %s"
          % ("turns", "decoys", "cond", "n", "mean", "range", "seeds",
             "think", "misses"))
    for at in sorted(cells):
        turns, rate, key = at
        scores = cells[at]
        tally = breakdown[at]
        thoughts = think[at]
        print("%-6d %-7s %-6s %-4d %-6.1f %-6s %-14s %-7d %s"
              % (turns, "%.0f%%" % (rate * 100), key, len(scores),
                 sum(scores) / float(len(scores)),
                 "%d-%d" % (min(scores), max(scores)),
                 " ".join(str(s) for s in scores),
                 sum(thoughts) // len(thoughts),
                 ", ".join("%d %s" % (v, k)
                           for k, v in sorted(tally.items()) if v) or "none"))


def main():
    global MAX_TOKENS
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", default="26,50,100,200")
    ap.add_argument("--questions", type=int, default=10)
    ap.add_argument("--decoys", type=float, default=0.0,
                    help="share of filler that is a confusable fact (0-1)")
    ap.add_argument("--decoy-rates", default="",
                    help="comma-separated rates; sweeps the whole grid over "
                         "each, so every density is measured under identical "
                         "settings and the cells are actually comparable")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", default="",
                    help="comma-separated seeds; repeats the whole grid on each")
    ap.add_argument("--no-full", action="store_true",
                    help="skip the full-transcript condition (it gets slow)")
    ap.add_argument("--no-pack", action="store_true",
                    help="skip the pack condition, to isolate the transcript")
    ap.add_argument("--out", default="long_bench_results.json")
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS,
                    help="generation budget; too small truncates reasoning")
    ap.add_argument("--filler",
                    choices=("synthetic", "daily", "daily-clean"),
                    default="synthetic",
                    help="synthetic: templated chatter. daily: real "
                         "human-written exchanges from DailyDialog, so the "
                         "conversation around the planted facts is not our "
                         "own generator. daily-clean: the same, minus "
                         "exchanges whose first-person identity claims "
                         "contradict the planted facts.")
    ap.add_argument("--budget", type=int, default=PACK_BUDGET,
                    help="pack size in characters; smaller packs carry fewer "
                         "rivals alongside the answer")
    args = ap.parse_args()

    MAX_TOKENS = args.max_tokens
    ask.__defaults__ = (600, MAX_TOKENS)

    lengths = [int(x) for x in args.turns.split(",") if x.strip()]
    seeds = ([int(x) for x in args.seeds.split(",") if x.strip()]
             or [args.seed])
    questions = FACTS[:args.questions]
    results = []

    print("%-7s %-6s %-7s | %-26s | %-26s"
          % ("turns", "seed", "knots", "full transcript", "qontext pack"))
    print("%-7s %-6s %-7s | %-6s %-6s %-5s %-6s | %-6s %-6s %-5s %-6s"
          % ("", "", "", "acc", "tokens", "sec", "think",
             "acc", "tokens", "sec", "think"))

    rates = ([float(x) for x in args.decoy_rates.split(",") if x.strip()]
             or [args.decoys])
    for rate in rates:
        if len(rates) > 1:
            print("\n-- decoys %.0f%%" % (rate * 100))
        for length in lengths:
            for seed in seeds:
                row = one_run(length, seed, rate, questions, args.no_full,
                              args.budget, args.no_pack, args.filler)
                results.append(row)
                report(row)

    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\nwrote %s" % args.out)

    summarise(results)

    # a plot you can read in a terminal
    print("\nprompt tokens per call")
    peak = max(e["tokens_per_call"] for r in results
               for e in (r.get("full"), r["pack"])
               if e and not e["void"]) or 1
    for row in results:
        for key in ("full", "pack"):
            entry = row.get(key)
            if not entry or entry["void"]:
                continue
            width = int(50.0 * entry["tokens_per_call"] / max(1, peak))
            print("  %-5d s%-4d %-5s %-6d %s"
                  % (row["turns"], row["seed"], key, entry["tokens_per_call"],
                     "#" * width))
    return 0


if __name__ == "__main__":
    sys.exit(main())
