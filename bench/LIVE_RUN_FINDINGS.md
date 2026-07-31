# First live benchmark with a model in the loop

Run 2026-07-28. Shirdel-Coder-9B-Claude-Fable-5.Q6_K on llama.cpp, Windows
Vulkan build, GPU (33/33 layers offloaded, ~35 tok/s). Ten factual questions
about a 26-turn conversation the memory had never seen.

| condition | accuracy | prompt tokens/call | share of full |
|---|---|---|---|
| full transcript | 10/10 | 476 | — |
| `quipu_memory` (the E4B's own) | 5/10 | 128 | 27% |
| `claude_memory` (hand-built ceiling) | 10/10 | 76 | 16% |
| **`qontext_memory` (the library)** | **10/10** | **69** | **14%** |
| no context | 1/10 | 52 | — |

## What this establishes

**Every number in this study before today was a containment proxy** — "does
the fact appear in the pack" — never "did the model answer". The proxy was
accurate: it predicted 10/10 for both the ceiling and the library, and that
is what happened.

**The library matches the hand-built ceiling at lower cost**, 69 tokens
against 76, on a pack that is denser (0.30 vs 0.41). The work done on
extraction and retrieval is real and not an artifact of substring matching.

**The model-built memory is genuinely worse** — 5/10 at nearly twice the
tokens. That reproduces the study's finding that extraction generalisation is
where small models fall down; it is not an artifact of the scoring either.

## What this contradicts

The report's headline (§4, F3) is that a small model scored **4/10 with the
full transcript against 10/10 with the pack** — context reduction as an
*accuracy* win, not merely a cost saving.

**That did not reproduce here. The full transcript scored 10/10.**

The difference is the model. The original result used Gemma E4B (~4B, no
reasoning). This run used a 9B reasoning model that spends ~100 tokens
thinking before answering, and it handles a 1,566-character transcript
without difficulty. "Small models drown in context" is a claim about *that*
model at *that* size, and the report should say so rather than implying it
generalises.

What survives, and is arguably the more useful claim for anyone deploying
this: **equal accuracy at 14% of the context cost.** Seven times cheaper per
call, with no measured loss. On a local model where context is also latency,
that is the whole argument.

## Instrumentation note

The first attempt at this run produced empty answers everywhere, including
the full-transcript condition, and looked like catastrophic failure. Cause:
the model emits reasoning tokens, llama.cpp puts them in a separate
`reasoning_content` field, and `max_tokens: 100` cut generation off before
any `content` was produced — 104 completion tokens for a two-word answer.
Raised to 512, and empty answers now report `finish_reason` and the length of
the reasoning so this cannot masquerade as a wrong answer again.

Worth remembering: the failure was indistinguishable from "the memory is
useless" until the raw JSON was inspected.


# The long-conversation run

Ten facts planted in the opening turns, buried under hundreds of turns of
unrelated chatter, then asked about. Five questions per length.

| turns | knots | transcript tokens | pack tokens | transcript acc | pack acc | transcript sec | pack sec |
|---|---|---|---|---|---|---|---|
| 26 | 13 | 304 | 65 | 4/5 | 5/5 | 0.7 | 0.2 |
| 100 | 50 | 1,114 | 65 | 4/5 | 5/5 | 1.4 | 0.1 |
| 400 | 200 | 4,516 | 65 | 5/5 | 4/5 | 2.3 | 0.1 |
| 800 | 400 | 9,081 | 65 | 5/5 | 5/5 | 2.7 | 0.1 |

**The pack is flat at 65 tokens across a 30x growth in the transcript**, and
accuracy holds on both sides. Offline the flatness continues to 1,600 turns
(~15,000 transcript tokens), where the memory hits its 500-knot ceiling,
evicts around eight hundred filler knots, and keeps all ten planted facts —
the importance-weighted eviction policy doing precisely its job on a test it
was never tuned against.

## Correction: the latency claim was wrong

An earlier draft asserted ~90 seconds of prompt processing for a 500-turn
transcript, extrapolated from `prompt_per_token_ms: 9.88` in a 39-token
request. That figure is dominated by fixed overhead and says nothing about
throughput. Measured:

| prompt tokens | seconds | tokens/second |
|---|---|---|
| 304 | 0.7 | 434 |
| 1,114 | 1.4 | 796 |
| 4,516 | 2.3 | 1,963 |
| 9,081 | 2.7 | 3,363 |

Real batch throughput is ~3,400 tokens/second, so the latency argument is
about thirty times weaker than claimed. The token argument is untouched: 140x
fewer tokens per call at 800 turns, which is KV cache, metered cost, and —
decisively — whether the prompt fits the window at all.

## Caveats

- Five questions per length is a small sample; the 4/5 entries on both sides
  are within noise of each other and should not be read as a difference.
- The filler is synthetic and structurally repetitive. Real conversation
  would produce messier knots, and the 500-knot ceiling would arrive sooner.
- Every measurement here is one model, one machine, one run.


# Adversarial decoys: the test that found two real bugs

The first long-conversation run used filler that could never be confused with
an answer — "the tram was strange again, number 42" does not compete with "my
dog is called Bikkel". Retrieval had a trivially easy job, and the 5/5 scores
were flattering.

`long_bench.py --decoys 0.6` replaces most of the filler with facts of the
same *shape* about other people: another person's dog, another team's project,
another day's standup, another tool for tracking tasks. That is what a long
real conversation actually looks like.

**Containment did not notice.** Still 10/10 at every decoy rate — the answer
was always in the pack. But 9 of 10 packs also carried one to four
competitors, which is a failure mode a substring check is structurally blind
to. The model has to *choose*.

Ranking the answer's position in the pack surfaced two genuine bugs.

## Bug 1: occupations in the synonym table

"What is the user's own job?" ranked *"Fenna works as a teacher"* and *"Sem
works as a teacher"* above *"the user works as a nurse"*. The `job` synonym
group contained `teaching`, `teacher` and `engineer`, added while tuning
against conversation B — whose user happens to be a teacher. The effect was
that a knot about *anyone* with that occupation outranked the real answer.

An occupation is payload, not a relation word. Removed from `job` and `role`,
with a test that fails if any specific occupation reappears in either group.

## Bug 2: ties handed the pack to the decoys

"Where does the user track tasks?" produced four knots scoring **exactly**
7.79 — three about other people, one about the user's team. The tiebreak is
recency, and the decoys were spoken later, so the user's own fact came last.

Subject match was only a tiebreak; it is now a multiplier (`SUBJECT_FOCUS`).
When the query names the user, knots with no first-party subject are damped.
That includes chained possessives: "the user's neighbour's dog" mentions the
user twice and is still the neighbour's dog, so a knot matching
`the (user|team)'s <word>'s` counts as third-party.

## Result

Position of the correct answer in the pack, across three seeds and two decoy
rates, on 800-turn conversations:

| | before | after |
|---|---|---|
| answer ranked first | 7/8 | **8/8** (all six runs) |

Guardrails all hold, and conversation C's noise *improved* as a side effect:
14% → 12% at budget 300. 91 tests green.

**What this says about method.** Two bugs had been sitting in the retrieval
path all day, invisible to every benchmark, because none of them contained a
plausible wrong answer. A memory that is never offered a tempting mistake
cannot be shown to resist one.


# The decoy run with a model in the loop — F3 reproduces

`long_bench.py --turns 400,800 --decoys 0.6 --questions 10`

| turns | transcript accuracy | transcript tokens | pack accuracy | pack tokens |
|---|---|---|---|---|
| 400 | **4/10** | 3,989 | **9/10** | 91 |
| 800 | **4/10** | 8,042 | **9/10** | 100 |

The full transcript loses to a hundred-token pack, badly. This is the study's
original F3 — that context reduction is an accuracy win and not merely a cost
saving — reproducing on a 9B reasoning model, after an earlier section of this
document concluded it was specific to a 4B non-reasoning model.

**The attribution is clean, because the control cell exists.** At 800 turns
with *no* decoys the transcript scored 5/5. At 800 turns with 60% decoys it
scores 4/10. Same length, same model, same questions-per-fact. Length is not
what breaks it.

**Confusable facts are.** A long conversation seeded with other people's dogs,
other teams' projects and other days' standups gives the model forty plausible
wrong answers, and it takes them. The pack hands over one candidate because
ranking already did the choosing — which is why the same memory that looked
merely cheaper at 26 turns looks decisive at 400.

## What this means for the claim

Three statements, in increasing order of precision:

1. *Original:* small models drown in context. Too broad — a 9B handled a
   26-turn transcript perfectly.
2. *First correction:* the inversion is specific to small non-reasoning
   models. Also wrong — the same 9B drowns here.
3. *Now:* **context reduction is an accuracy win when the context contains
   plausible wrong answers.** Model size and conversation length are both
   secondary; distractor density is the variable that matters.

The third version is the one worth defending, and it is the one that
generalises to real use, because a real long conversation is nothing but
accumulated near-misses.

## Replicated, with a matched control

Three seeds at 60% decoys, plus the 0% control at the same ten questions:

| 800 turns | transcript | pack |
|---|---|---|
| no decoys | **10/10** | 10/10 |
| 60% decoys, seed 7 | 4/10 | 9/10 |
| 60% decoys, seed 11 | 5/10 | 10/10 |
| 60% decoys, seed 23 | 2/10 | 8/10 |

Transcript: 10/10 without distractors, **mean 3.7/10 with them**. Pack: 10/10
without, **mean 9/10 with**. Same length, same model, same questions. The
variable is distractor density and nothing else.

## The mechanism was not what I claimed

An earlier draft of this section said the model "takes" the plausible wrong
answers. The miss detail says otherwise:

```
full missed Where does the user live?   answered: 'unknown'
full missed What is the user's job?     answered: 'unknown'
full missed What is the dog's name?     answered: ''
```

It does not answer "Rex" instead of "Bikkel". It fails to locate Bikkel at
all, and reports that honestly. The distractors do not seduce the model —
**they camouflage the target**. This is lost-in-the-middle, with the
distractors raising the noise floor rather than baiting a specific error.

The empty strings are a second and distinct failure: with forty candidates to
weigh, the model spends its entire 512-token generation budget reasoning and
never reaches an answer. `long_bench.py` now reports `finish_reason` and the
length of the reasoning for empty answers, so the two can be told apart.

Seduction does happen — but on our side of the fence. The pack's misses at
seed 23 were `'unknown'` for the codename and, for tasks tracking, *"Tasks are
tracked in Jira, Base…"*: a pack that leaked several competing tools and a
model that duly listed them. That is the failure mode `SUBJECT_FOCUS` reduced
and did not eliminate.

## Still open

- Three seeds is enough to see a large effect and not enough to put an
  interval on it.
- Whether the empty answers are budget exhaustion or something else is now
  measurable but not yet measured.
- Everything here is one model on one machine.


# The distractor curve — a cliff, not a slope

Sweeping decoy density at 800 turns, ten questions each:

| decoys | transcript | pack | transcript tokens | pack tokens |
|---|---|---|---|---|
| 0% | **10/10** | 10/10 | 9,066 | 66 |
| 10% | **5/10** | 9/10 | 8,863 | 83 |
| 30% | **2/10** | 10/10 | 8,521 | 99 |
| 60% (seed 7) | 4/10 | 9/10 | 8,042 | 100 |
| 60% (seed 11) | 5/10 | 10/10 | 7,922 | 96 |
| 60% (seed 23) | 2/10 | 8/10 | 8,011 | 99 |

**One in ten messages being a confusable fact is enough to halve transcript
accuracy.** The collapse is not gradual — it happens immediately and then
stays down. The pack holds 8-10 across the entire range, and its token cost
barely moves (66 to 100).

This is the number that matters for deployment, because a real conversation
is far past 10% distractor density. Every project you have ever mentioned,
every colleague, every deadline that later moved — all of it is a near-miss
for something you will eventually ask about.

## Three failure modes, and which one appears depends on density

The miss detail shows the transcript failing in three distinct ways:

**Seduction** — answering with a decoy, confidently:

```
What is the project codenamed?  answered: 'fox-signal'      (real: heron-nest)
When is the report due?         answered: 'September 12th.' (real: March 3rd)
What is the project codenamed?  answered: 'amber-lattice'   (real: heron-nest)
```

**Surrender** — `'unknown'`, the honest failure.

**Deliberation collapse** — `finish_reason=length` after 1,500 to 2,500
characters of reasoning, with no answer produced at all. The model weighs the
candidates until it runs out of budget. This was hypothesised earlier in this
document and is now confirmed directly.

Which mode dominates shifts with density. At 10-30% decoys the model still
commits, and commits wrongly. At 60% it mostly gives up or thinks itself out
of time. So both of my earlier characterisations were half right and stated
too absolutely: seduction at low density, camouflage at high.

## Correction to the correction

An earlier section of this document asserts "the model does not answer 'Rex'
instead of 'Bikkel'". At 60% decoys that was accurate. At 10% and 30% it is
plainly false — `fox-signal`, `amber-lattice` and `September 12th` are decoys
answered as fact. The general statement should be: **a context full of
near-misses degrades the answer, by one of three mechanisms, and which one
you get depends on how crowded the context is.**

## What the pack does about it

Nothing clever — it removes the choice. Ranking discards the rivals before
the model sees them, so there is nothing to be seduced by, nothing to search
through, and nothing to deliberate over. That is why its accuracy is flat
across a density range that takes the transcript from 10/10 to 2/10.


# Retraction: the density table above was measured through a broken instrument

Every number in the section above ran at `max_tokens: 512`. That cap is below
what this model needs to finish reasoning about a crowded context, so a large
share of the transcript's "misses" were generations truncated mid-thought.
The condition that deliberates is the condition that gets truncated, so the
damage fell almost entirely on one side of the comparison.

The scale of the error, same seed, same conversation, same questions:

| 800 turns, 10% decoys, seed 7 | transcript |
|---|---|
| at `max_tokens: 512` | 5/10 |
| at `max_tokens: 4096` | **9/10** |

Nearly half the reported failure was the harness. **The published cliff was
partly an artifact of my own measurement**, and the table above should not be
cited. What follows replaces it.

Three lessons, since this is the third time the same class of bug has
produced a finding:

1. An empty answer is not a wrong answer, and a benchmark that cannot tell
   them apart will report instrument limits as results. `classify()` now
   splits every miss into **empty / unknown / wrong**.
2. A cap you cannot see is a cap you will hit. `thinking_per_call` is now
   measured and printed, so the ceiling is visible as it is approached.
3. A transport failure is not a score of zero. When every call in a cell
   fails, the cell reports **VOID** and is excluded from every average.


# The density sweep, re-measured

`long_bench.py --turns 800 --decoy-rates 0,0.1,0.3,0.6 --seeds 7,11,23
--questions 10`, all cells at `max_tokens: 4096`.

| decoys | transcript | pack | gap | transcript reasoning | pack reasoning |
|---|---|---|---|---|---|
| 0% | 9.3 (9-10) | **10.0** (10-10) | +0.7 | 1,166 ch | 669 ch |
| 10% | 6.7 (4-9) | **8.0** (7-9) | +1.3 | 2,606 ch | 2,123 ch |
| 30% | 3.7 (3-4) | **6.7** (6-7) | +3.0 | 5,290 ch | 2,190 ch |
| 60% | 4.3 (3-6) | **8.0** (6-10) | +3.7 | 5,358 ch | 658 ch |

Three corrections to the earlier account, and one new finding.

**It is a slope, not a cliff.** The transcript degrades progressively from 0%
to 30%. The "immediate collapse at 10%" was the truncation artifact.

**The pack is not immune, and earlier sections implying otherwise were
quoting only the good cells.** It falls from 10.0 to 6.7 at 30% decoys. What
survives is the *comparison*: the pack wins at every density and the margin
grows monotonically, +0.7 → +1.3 → +3.0 → +3.7.

**Every one of the pack's losses is the model, not the memory.** Containment
was checked offline at all four densities: **30/30 at each**, with the correct
knot ranked first. The pack's misses are the model hedging (`unknown`) or
deliberating past its budget while holding the right answer on line one.

**New: crowding inflates *generated* tokens, which are the expensive kind.**
The reasoning column had never been measured. A crowded transcript makes the
model think 4.6x longer (1,166 → 5,358 characters); the pack's stays flat and
at 60% decoys is 8x smaller. Every previous cost argument in this document
concerned prompt tokens only, and therefore understated the difference.

## The cap is not the explanation — tested directly

The obvious objection to the table above is that 4096 still binds: reasoning
reached 19,350 characters, exactly the ceiling. If the empties were merely
unconverged, transcript accuracy would recover with more room.

`--turns 800 --decoy-rates 0.3 --seeds 7,11,23 --no-pack --max-tokens 12288`:

| 800 turns, 30% decoys | accuracy | reasoning per call | empty | wrong |
|---|---|---|---|---|
| `max_tokens: 4096` | 3.7 (3-4) | 5,290 ch | 11 | 3 |
| `max_tokens: 12288` | **4.3 (4-5)** | **14,926 ch** | 16 | 1 |

**Tripling the generation budget bought +0.6 out of 10 and cost 2.8x the
reasoning.** Individual questions ran to 37,699 characters of thinking and
still produced no answer. The model does not converge given more room; it
deliberates further. The transcript's failure is therefore real and not an
artifact, which is what this run was for.

It also shifts the failure mode rather than removing it: `wrong` answers fall
from 3 to 1 while `empty` rises from 11 to 16. With more room the model stops
committing to decoys and starts never finishing — a different failure, not a
smaller one.

The cost framing is now the sharper one. At 30% decoys the transcript spends
roughly 3,700 generated tokens per question — around a hundred seconds on
this machine — to score 4.3/10. The pack spends a fraction of that, on ~100
prompt tokens, to score 6.7/10. **It is not cheaper-and-equal, and not even
cheaper-and-better; it is dramatically cheaper and better at once.**

## What is still one machine, one model

Every figure here comes from a single 9B reasoning model on one desktop, with
synthetic conversations whose filler is structurally repetitive. Three seeds
per cell gives ranges wide enough (3-6 in one case) that no single cell should
be quoted to one decimal place without them.


# A second model, and F3 does not reproduce

Everything above is one model. Running the same sweep against a second —
Gemma-4-12B-it Q8_0, 32K context, same machine, same conversations, same
questions — gives a result the report has to absorb rather than explain away.

| 800 turns, 3 seeds | transcript | pack | transcript tokens | pack tokens |
|---|---|---|---|---|
| 0% decoys | **10.0** | 10.0 | 9,034 | 67 |
| 10% decoys | **10.0** | 10.0 | 8,838 | 87 |
| 30% decoys | **10.0** | 10.0 | 8,476 | 99 |
| 60% decoys | **9.7** | 10.0 | 7,945 | 99 |

**One miss in 120 questions**, and it was a deliberation collapse rather than
a wrong answer. The distractor cliff, the slope, the seduction and camouflage
failure modes — none of it appears. The larger model simply reads an 800-turn
transcript containing forty plausible wrong answers and picks the right one.

I predicted before this run that the 12B would "degrade less steeply" and that
the pack's margin would still grow with density. Both were wrong: it does not
degrade, and the margin is zero everywhere.

## What this does to the claim

F3 said: *context reduction is an accuracy win when the context contains
plausible wrong answers.* That was already the third version of the statement.
It now has a clean counterexample, and the fourth version has to name the
missing variable:

> Context reduction is an accuracy win when the context contains plausible
> wrong answers **and the model is at or past its ability to choose among
> them**. Distractor density sets the difficulty; model capability sets the
> threshold. Neither alone predicts the inversion.

Note what is *not* established. The two models differ in parameters, family,
training and quantisation at once, so "bigger models are immune" is not what
was measured — only that this 12B is unaffected where that 9B collapsed. The
variable might be size, might be reasoning quality, might be neither.

## What survives, and it is not nothing

**The cost result is untouched and is now the whole claim.** Identical
accuracy — 10/10 against 10/10 — at 67 to 99 tokens against 7,900 to 9,000.
That is a **90x to 135x reduction in prompt tokens for no measurable loss**,
on the model where the accuracy argument fails. A technique that costs one
percent of the context and gives up nothing does not need an accuracy story.

**The reasoning cost reproduces.** Crowding still inflates generated tokens on
the 12B — transcript reasoning grows 425 to 1,918 characters as density rises,
4.5x, while the pack's grows 503 to 1,308. Smaller in absolute terms than the
9B's 5,000+, same direction. Generated tokens are the expensive ones, so this
is a real cost difference even where accuracy is tied.

**And the deployment argument is stronger, not weaker.** The 12B needed a
32K context to hold the transcript at all; the pack needed 99 tokens. The
question "does it fit, and what does it cost" does not go away because a
capable model answers well when it does fit.

## The honest summary

On a 9B, Qontext is an accuracy win. On a 12B, it is a 100x cost saving at
parity. Both are worth having and they are different claims, and the report
should stop implying the first one generalises.


# Real conversation: the last synthetic thing removed

Every benchmark conversation up to this point was generated by our own script.
`--filler daily` replaces the templated chatter around the planted facts with
30,000 human-written exchanges from DailyDialog, kept as consecutive
(utterance, reply) pairs so real conversational adjacency survives. 550
exchanges were dropped for containing our answer keywords — "Friday" occurs
226 times in the raw corpus, and left in, a wrong transcript answer could
match a keyword by coincidence with the scorer unable to tell.

## The construction error, found before it was published

Splicing unrelated dialogues under one speaker does something no real
conversation does: 13.6% of exchanges make a first-person identity claim, and
the extractor faithfully turns them into knots about *the user* — "the user is
Monica", "the user is Greg Wu, Head of Consultancy", "the user is from the
plains of the Midwest". Those do not compete with the planted facts, they
contradict them. One speaker does not have three names.

Offline containment caught it: 9/10 with the raw splice, **10/10 on all three
seeds** once those exchanges are filtered (`--filler daily-clean`).

## The decomposition

800 turns, 12B, no decoys, three seeds, identical questions:

| filler | transcript | pack |
|---|---|---|
| synthetic | 10.0 | 10.0 |
| real, cleaned | **9.3** (9-10) | **9.7** (9-10) |
| real, with splice contradictions | 8.7 (8-9) | 8.7 (8-9) |

**Real conversation costs about half a point. The construction error cost
another 0.7.** An earlier draft of this section claimed real chatter was a far
stronger stressor than every adversarial decoy we built; that was half true and
half our own bug, and the corrected magnitude is much smaller.

What does survive that comparison: real filler at **zero** decoys (9.3) is
still harder than synthetic filler at **60%** decoys (10.0). Days of decoy
engineering produced a weaker stressor than ordinary human conversation, which
says more about the decoys than about the models.

## The headline result, on non-synthetic conversation

800 turns, 12B, real cleaned filler, three seeds:

| | transcript | pack | ratio |
|---|---|---|---|
| accuracy | 9.3 (9-10) | 9.7 (9-10) | tied |
| prompt tokens | 13,853 | 116 | **119x** |
| reasoning generated | 1,697 ch | 789 ch | 2.1x |
| seconds of prompt processing | 6.6 | 0.4 | 17x |

Accuracy is tied — ranges overlap and n is three, so the pack's nominal lead
is not a result. **The saving is the result**: the same answers for one
percent of the prompt and half the generated reasoning.

The transcript's two misses were both deliberation collapse on *"What is the
user's name?"* — 13,000 to 14,500 characters of reasoning, no answer. Real
dialogue is full of names, and the model spent its entire generation budget
weighing them. The pack's single miss was the same question answered "Terry
Graham" from a knot the ranking had placed below the correct one.

## What is still synthetic

The ten planted facts and the ten questions. The environment is real; the
probe is ours. Removing that too requires either an answer key somebody else
wrote, or a key-free evaluation of the kind `turing_bench.py` attempts.
