#!/usr/bin/env python3
"""
An imitation game for a memory, after Turing's answer to a question we asked.

Every other benchmark here scores answers against a key of ten planted facts.
Qontext stores facts. So the exam was written out of the syllabus we taught,
and 30/30 containment proves only that we asked about the things we keep. It
measures conformity, not comprehension.

This replaces the key with a game. Two answerers sit behind a curtain:

    participant   a model that can read the whole transcript — it "was there"
    memory        the same model reading only a Qontext pack

An interrogator asks questions about the conversation. A judge sees both
answers, in random order, and says which came from the participant. If the
judge cannot tell, the memory has passed an operational test of having
understood the conversation *to the degree the participant did*. No answer
key is manufactured at any point, which is what makes this runnable on real
conversations whose contents nobody may annotate.

Questions come in four tiers, because factual recall is the easy part:

    fact            what was decided, when, by whom
    inference       what was the unresolved tension; what changed direction
    counterfactual  if X had not happened, what would likely have followed
    revision        what changed during the talk; which two facts, together,
                    forced a decision neither would have forced alone

Qontext is expected to lose the upper tiers. Its three design rules — name
the subject, the payload is the point, fewer and better knots — throw away
tone, sequence, and everything not shaped like a fact. That loss has never
been measured, only assumed harmless.

The fourth tier exists because a flat set of propositions can restate what
was said and still not represent that one remark *revised* another. That is
what qontext_cords.py adds and what `--mode both` compares.

A caution about the foil, since it decides what a pass means. The
"participant" here is a model reading the whole transcript, not a human.
On a crowded 800-turn transcript that model scores 3.7/10 on plain facts —
so passing against it there would mean nothing. The default conversation is
44 turns with no distractors, where the transcript reader is near ceiling
and the comparison is honest. Treat --pad as making the foil worse, not the
test harder.

    python turing_bench.py                     # 16 questions, 4 per tier
    python turing_bench.py --trials 6          # fewer, faster
    python turing_bench.py --pad 600           # bury it in 600 turns of chatter
    python turing_bench.py --show              # print every answer pair

Two instrument checks run automatically, because this project has now had
three findings turn out to be harness bugs:

  * a calibration round where BOTH answers come from the participant. A fair
    judge scores 50% there. Anything else is position or style bias, and the
    real number has to be read against it.
  * answers are truncated to the same word count before judging, so the judge
    cannot simply notice that one side is terser.
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
from qontext_cords import CordMemory  # noqa: E402

API_URL = "http://127.0.0.1:8080/v1/chat/completions"
PACK_BUDGET = 300
MAX_TOKENS = 4096
# Measured in the first live run: revision questions drew 18,000-20,000
# characters of reasoning from the answerer and 9,000-11,000 from the judge,
# against caps of 4,096 and 2,048 tokens. Six of sixteen questions went
# unscored, and — worse — they were not a random six. The hardest tier lost
# the most, and the two modes lost *different* questions, so the comparison
# was across different question sets.
ANSWER_TOKENS = 8192
JUDGE_TOKENS = 4096
RETRIES = 1                # one more attempt before giving up on a question
ANSWER_WORDS = 25          # both sides are cut to this before judging

# A conversation with somewhere to go. The long_bench transcript is ten facts
# in a field of "the tram was strange again, number 42" — nothing happens in
# it, so there is no tension to notice and no direction to change. Tier-2 and
# tier-3 questions need a conversation that actually moves.
CONVERSATION = [
    ("user", "Morning. We should lock the launch date today, it's dragging."),
    ("assistant", "Agreed. Where did we land last time?"),
    ("user", "March 3rd. Everyone nodded, nobody was thrilled."),
    ("assistant", "Nodding isn't agreement. What wasn't said?"),
    ("user", "Priya thinks QA is thin. She didn't push, she just went quiet."),
    ("assistant", "Quiet from Priya usually means she's already done the maths."),
    ("user", "Probably. Anyway, March 3rd works for marketing, that's the point."),
    ("assistant", "Is marketing the constraint, or the loudest voice?"),
    ("user", "Fair. It's the loudest. But the campaign spend is committed."),
    ("assistant", "Then the date is committed too, unless the spend can move."),
    ("user", "It can't. Contract signed in January."),
    ("assistant", "Understood. So what's the actual risk to March 3rd?"),
    ("user", "The auth rewrite. It's the only thing on the critical path."),
    ("assistant", "How far along is it?"),
    ("user", "Sixty percent, and that's Tomas's estimate, which I'd discount."),
    ("assistant", "Discount by how much?"),
    ("user", "He said sixty last month too."),
    ("assistant", "So it's stalled and nobody has said so out loud."),
    ("user", "Hm. Yes. That's the honest version."),
    ("assistant", "Then March 3rd isn't a plan, it's a hope with a contract attached."),
    ("user", "That's harsh. Accurate, but harsh."),
    ("assistant", "What would make it a plan?"),
    ("user", "Cutting auth from the launch. Ship read-only, add accounts in April."),
    ("assistant", "Does the campaign promise accounts?"),
    ("user", "...it does. In the headline."),
    ("assistant", "Then that's the decision. Not the date — the scope."),
    ("user", "God. Okay. I've been arguing about the wrong thing for three weeks."),
    ("assistant", "Three weeks of date meetings, and the scope was the variable."),
    ("user", "Right. Let me think. If we cut accounts, marketing has to redo the headline."),
    ("assistant", "Which costs what, exactly?"),
    ("user", "Goodwill, mostly. Sara redid it once already in December."),
    ("assistant", "So the cost is Sara's patience, not money."),
    ("user", "Yes. And I keep spending that instead of the budget, because it's free."),
    ("assistant", "It isn't free. It's just billed later."),
    ("user", "Noted. Painfully."),
    ("assistant", "So: cut accounts, keep March 3rd, redo the headline?"),
    ("user", "Keep March 3rd. Cut accounts. I'll tell Sara myself, not over email."),
    ("assistant", "And Priya?"),
    ("user", "She'll be vindicated and too polite to say it."),
    ("assistant", "You could just say it for her."),
    ("user", "I could. I probably won't."),
    ("assistant", "Then that's the part still unresolved."),
    ("user", "Yeah. Let's move. Tomas gets the auth deadline pushed to April 14th."),
    ("assistant", "April 14th for auth, March 3rd for launch, read-only."),
]

# Built-in questions, used when the interrogator cannot be generated. They are
# NOT an answer key — nothing here records what a right answer would be.
FALLBACK = [
    ("fact", "What launch date was finally agreed?"),
    ("fact", "What was cut from the launch scope?"),
    ("fact", "What new deadline was set for the auth work?"),
    ("fact", "Who redid the campaign headline once already?"),
    ("inference", "What was the unresolved tension at the end?"),
    ("inference", "Which remark most changed the direction of the conversation?"),
    ("inference", "Why had the previous three weeks been wasted?"),
    ("inference", "What did the speaker realise they had been treating as free?"),
    ("counterfactual", "If the campaign contract had not been signed, what would "
                       "likely have been decided instead?"),
    ("counterfactual", "If Priya had argued openly at the earlier meeting, how "
                       "would this conversation have differed?"),
    ("counterfactual", "If Tomas's estimate had been trusted, what would the "
                       "outcome have been?"),
    ("counterfactual", "Had the headline not mentioned accounts, what would have "
                       "changed?"),
    ("revision", "What did the speaker believe at the start that they no "
                 "longer believed by the end?"),
    ("revision", "Which position was abandoned during the conversation, and "
                 "what replaced it?"),
    ("revision", "What did one remark reveal about an earlier one that was "
                 "not apparent at the time?"),
    ("revision", "Which two facts, taken together, forced the decision that "
                 "neither would have forced alone?"),
]

TIERS = ("fact", "inference", "counterfactual", "revision")

INTERROGATOR = (
    "You are devising questions to test how well someone remembers a "
    "conversation they took part in. Write %d questions of type '%s'.\n"
    "  fact: a concrete detail that was stated.\n"
    "  inference: something true of the conversation that was never stated "
    "outright — a tension, a turning point, an unspoken motive.\n"
    "  counterfactual: what would plausibly have happened had some part of "
    "it gone differently.\n"
    "  revision: how a position, belief or interpretation CHANGED during "
    "the conversation, or how two remarks constrain each other.\n"
    "Output one question per line, no numbering, no commentary."
)

ANSWERER = ("You took part in the conversation below. Answer the question "
            "from what you remember. Be specific. At most %d words. Do not "
            "explain your reasoning." % ANSWER_WORDS)

JUDGE = (
    "Two people were asked the same question about a conversation. One of "
    "them actually took part in it. The other has only a compressed set of "
    "notes about it.\n"
    "Decide which answer came from the person who was there.\n"
    "Both answers may be correct, or both wrong — judge only who was "
    "present.\n"
    "Reply with exactly one character: A or B."
)


def call(system, user, max_tokens=MAX_TOKENS, temperature=0.3):
    body = json.dumps({
        "model": "local",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature, "top_k": 64, "top_p": 0.95,
        "max_tokens": max_tokens,
    }).encode()
    request = urllib.request.Request(
        API_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            data = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as error:
        return None, "%s" % type(error).__name__
    message = data["choices"][0]["message"]
    text = (message.get("content") or "").strip()
    if not text:
        thinking = message.get("reasoning_content") or ""
        return None, "empty (finish=%s, %d chars thinking)" % (
            data["choices"][0].get("finish_reason"), len(thinking))
    return text, None


def clip(text, words=ANSWER_WORDS):
    """Same length for both sides, or the judge just detects terseness."""
    parts = re.sub(r"\s+", " ", text).strip().split(" ")
    return " ".join(parts[:words])


def interrogate(transcript, per_tier):
    """Ask the model to devise the questions, so we never write a key."""
    questions = []
    seen = set()
    for tier in TIERS:
        text, problem = call(INTERROGATOR % (per_tier, tier),
                             "Conversation:\n%s" % transcript,
                             max_tokens=1024, temperature=0.8)
        lines = []
        if text:
            for line in text.splitlines():
                line = line.strip().lstrip("-*0123456789. ").strip()
                key = line.lower()
                if line.endswith("?") and len(line) > 15 and key not in seen:
                    seen.add(key)
                    lines.append(line)
        if len(lines) < per_tier:
            spare = [q for t, q in FALLBACK if t == tier]
            lines += spare[len(lines):per_tier]
            if problem:
                print("  interrogator fell back for %s (%s)" % (tier, problem))
        questions += [(tier, q) for q in lines[:per_tier]]
    return questions


def answer(context, question):
    for attempt in range(RETRIES + 1):
        text, problem = call(ANSWERER,
                             "Conversation:\n%s\n\nQuestion: %s"
                             % (context, question),
                             max_tokens=ANSWER_TOKENS,
                             temperature=0.3 + 0.2 * attempt)
        if text:
            return clip(text), None
    return "[no answer]", problem


def adjudicate(question, first, second):
    for _ in range(RETRIES + 1):
        text, problem = call(
            JUDGE,
            "Question: %s\n\nAnswer A: %s\n\nAnswer B: %s"
            % (question, first, second),
            max_tokens=JUDGE_TOKENS, temperature=0.0)
        if text:
            found = re.search(r"\b([AB])\b", text.upper())
            if found:
                return found.group(1), None
    return None, problem


GROUNDING = (
    "Below is a conversation, then a statement about it. Reply with exactly "
    "one word: SUPPORTED if everything the statement asserts is borne out by "
    "the conversation, or UNSUPPORTED if any part of it is invented, "
    "contradicted, or cannot be checked against what was said. A statement "
    "that merely declines to answer is SUPPORTED."
)


def grounded(transcript, statement):
    """Is this answer actually true of the conversation?

    The imitation game rewards plausibility, not truth — a memory that
    confabulates fluently beats one that honestly reports having nothing.
    Observed directly in the first live run: the weave answered "the speaker
    believed the contract was signed in January, but later realised it was
    not", which is false and which fooled the judge, while the flat pack
    answered "no position was abandoned", which is also false but sounded
    like notes and was caught.

    This checks each answer against the source. It needs no answer key —
    only the transcript — so it stays usable on conversations nobody may
    annotate.
    """
    text, _problem = call(GROUNDING,
                          "Conversation:\n%s\n\nStatement: %s"
                          % (transcript, statement),
                          max_tokens=JUDGE_TOKENS, temperature=0.0)
    if not text:
        return None
    return "UNSUPPORTED" not in text.upper()


def play(rounds, label, transcript, get_memory_context, rnd, show):
    """One imitation game. Returns {question: outcome}, one entry per ask.

    `hit` is True when the judge identified the participant, False when it was
    fooled, and None when the round could not be scored — kept rather than
    dropped so the caller can compare modes on the questions both completed.
    """
    outcomes = {}
    for tier, question in rounds:
        participant, problem_p = answer(transcript, question)
        if label == "calibration":
            # Both sides are the participant. A fair judge is at chance here;
            # if it is not, it is reading style or position, not presence.
            other, problem_m = answer(transcript, question)
        else:
            other, problem_m = answer(get_memory_context(question), question)
        entry = {"tier": tier, "hit": None, "participant": participant,
                 "memory": other}
        outcomes[question] = entry
        if problem_p or problem_m:
            print("  unscored %-14s %s" % (tier, problem_p or problem_m))
            continue
        swap = rnd.random() < 0.5
        first, second = (other, participant) if swap else (participant, other)
        truth = "B" if swap else "A"
        pick, problem = adjudicate(question, first, second)
        if pick is None:
            print("  unscored %-14s judge: %s" % (tier, problem or "no verdict"))
            continue
        hit = pick == truth
        entry["hit"] = hit
        if show:
            print("  %-14s %s" % (tier, question))
            print("     participant: %s" % participant)
            print("     memory     : %s" % other)
            print("     judge %s -> %s" % (pick, "identified" if hit
                                           else "fooled"))
    return outcomes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=16,
                    help="questions in total; split evenly across the four tiers")
    ap.add_argument("--pad", type=int, default=0,
                    help="bury the conversation under this many total turns")
    ap.add_argument("--budget", type=int, default=PACK_BUDGET)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mode", choices=("pack", "weave", "both"),
                    default="pack",
                    help="pack: the shipped ranked list. weave: seed on "
                         "relevance then follow the cords (qontext_cords). "
                         "both: play the game twice and compare.")
    ap.add_argument("--show", action="store_true", help="print every pair")
    ap.add_argument("--no-calibration", action="store_true")
    ap.add_argument("--grounding", action="store_true",
                    help="also check each memory answer against the "
                         "transcript, since fooling the judge and being "
                         "right are not the same thing")
    ap.add_argument("--out", default="turing_bench_results.json")
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    conversation = list(CONVERSATION)
    if args.pad:
        import long_bench
        filler = long_bench.build(args.pad, args.seed, 0.0)
        conversation += filler[:max(0, args.pad - len(conversation))]
    transcript = "\n".join("%s: %s" % (who, t) for who, t in conversation)

    # CordMemory is a QontextMemory that also records how knots hang together,
    # so one instance serves both modes and the comparison is like-for-like.
    memory = CordMemory()
    for who, text in conversation:
        memory.observe(who, text)

    print("conversation: %d turns, %d chars -> %d knots, %d cords"
          % (len(conversation), len(transcript), len(memory),
             memory.stats()["cords"]))
    print("devising questions (the interrogator writes them, not us)")
    questions = interrogate(transcript, max(1, args.trials // len(TIERS)))
    for tier, question in questions:
        print("  %-14s %s" % (tier, question))

    modes = (("pack", "weave") if args.mode == "both" else (args.mode,))
    contexts = {"pack": lambda q: memory.pack(q, args.budget),
                "weave": lambda q: memory.pack_weave(q, args.budget)}
    rounds = {}
    for mode in modes:
        print("\nthe game — %s" % mode)
        rounds[mode] = play(questions, "game", transcript, contexts[mode],
                            rnd, args.show)

    calibration = None
    if not args.no_calibration:
        print("\ncalibration (both answers from the participant; 50% is fair)")
        cal = play(questions, "calibration", transcript, None, rnd, False)
        scored = [e["hit"] for e in cal.values() if e["hit"] is not None]
        calibration = (sum(scored), len(scored))

    # Only questions every mode managed to score. An unscored question is not
    # a random loss — the hardest tiers lose the most, and the modes lose
    # different ones — so comparing raw totals compares different exams.
    paired = [q for _tier, q in questions
              if all(rounds[m].get(q, {}).get("hit") is not None
                     for m in modes)]
    dropped = len(questions) - len(paired)

    print("\n%-16s %-8s %s" % ("tier", "judged", "identified the participant"))
    for mode in modes:
        if len(modes) > 1:
            print("-- %s" % mode)
        by_tier = {}
        for question in paired:
            entry = rounds[mode][question]
            seen, won = by_tier.get(entry["tier"], (0, 0))
            by_tier[entry["tier"]] = (seen + 1, won + entry["hit"])
        for tier in TIERS:
            seen, won = by_tier.get(tier, (0, 0))
            if seen:
                print("%-16s %-8d %d  (%.0f%%)" % (tier, seen, won,
                                                   100.0 * won / seen))
        total = sum(s for s, _ in by_tier.values())
        right = sum(w for _, w in by_tier.values())
        if total:
            print("%-16s %-8d %d  (%.0f%%)" % ("ALL", total, right,
                                               100.0 * right / total))
    if calibration and calibration[1]:
        print("%-16s %-8d %d  (%.0f%%)  <- instrument bias"
              % ("calibration", calibration[1], calibration[0],
                 100.0 * calibration[0] / calibration[1]))
    if dropped:
        print("\n%d of %d question(s) unscored in at least one mode and "
              "excluded from every column above." % (dropped, len(questions)))
    if len(paired) < 12:
        print("With %d paired questions, a difference of one or two between "
              "modes is noise.\nRaise --trials before reading anything into "
              "it." % len(paired))

    if args.grounding:
        print("\ngrounding — is the answer actually true of the "
              "conversation?")
        print("(the game rewards plausibility; this catches fluent "
              "confabulation)")
        for mode in modes:
            ok = checked = 0
            for question in paired:
                verdict = grounded(transcript, rounds[mode][question]["memory"])
                if verdict is None:
                    continue
                checked += 1
                ok += verdict
            if checked:
                print("  %-6s %d/%d supported by the transcript (%.0f%%)"
                      % (mode, ok, checked, 100.0 * ok / checked))

    print("\nReading it: 50% means the judge cannot tell, so the memory "
          "answered\nas well as being there did. 100% means Qontext is "
          "visibly a set of notes.\nCompare against the calibration line "
          "before believing either.")

    Path(args.out).write_text(json.dumps(
        {"turns": len(conversation), "knots": len(memory),
         "cords": memory.stats()["cords"], "questions": questions,
         "paired": paired, "unscored": dropped,
         "modes": {mode: rounds[mode] for mode in modes},
         "calibration": calibration}, indent=2), encoding="utf-8")
    print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
