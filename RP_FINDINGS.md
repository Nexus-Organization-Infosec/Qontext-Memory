# What roleplay prose does to the Qontext extractor

Measured 2026-07-28 with `rp_probe.py` over 12 unique public RP logs
(13 files, two byte-identical), 4,569 turns and ~570 KB of prose. The logs are
plain text: a title line, then blank-line-separated paragraph blocks of
200–900 characters, **with no speaker labels** — turn boundaries exist,
attribution does not.

No content from the logs is reproduced here or anywhere in the repo; only
statistics and structural categories.

## The headline

Density is **0.43** on average (benchmark conversations: 0.30–0.48), so the
idea survives contact with prose. Precision does not. Of 3,365 knots:

| category | share | what it means |
|---|---|---|
| broken mid-quote | 17.3% | `_trim` cut inside a quotation — a design-rule-2 violation, and the payload is often what got cut |
| pure dialogue line | 15.2% | a quoted utterance stored as if it were a fact |
| hit the 120-char cap | 16.0% | truncated at `MAX_ENTRY_CHARS`; prose sentences are long, so the payload can fall off the end |
| admitted by payload alone | 33.7% | no marker fired; it got in because *something was capitalised*, which in fiction is every character and place |
| admitted by marker alone | 47.5% | a marker fired with no concrete payload |
| rewritten to "the user" | 17.6% | speaker collapse — every subject becomes the same person |

Mean knot length is 68 chars. One log of twelve (log8, 571 knots) exceeds the
default 500-knot cap in a single session, so eviction engages in long
roleplay — which makes eviction quality, not capacity, the thing to watch.

Roughly a third of knots are structurally broken (mid-quote or bare
dialogue). Much of the payload-alone third is dubious on top of that.

## Why prose breaks it

The extractor was built for clipped, factual, first-person, single-speaker
chat. Roleplay violates every one of those assumptions:

- **Quotation.** Dialogue is 15–39% of sentences. Nothing in the extractor
  knows a quotation is a unit, so it splits and trims through them.
- **Capitalisation is noise, not signal.** `_has_payload` treats a
  mid-sentence capital as evidence of a fact. In fiction, 3–49% of sentences
  per log carry one, and they are character names in ordinary narration.
- **Sentences are long.** 39–108 chars average per log, against ~60 in the
  benchmark, so the 120-char knot cap truncates real content.
- **Facts are about entities, not "the user".** 17.6% of knots claim the
  user did something a character did.

## Proposed RP rules (to implement and measure next)

1. **Quotations are atomic.** Never split or trim inside one. Fixes 17.3%
   outright and is a strict improvement for the chat case too.
2. **A bare utterance is not a fact.** Either drop knots that are only a
   quoted line, or convert them to attributed form ("Sabine says she will be
   at the shrine by the new moon"). Attribution is better memory but needs a
   speaker, which these logs lack and SillyTavern supplies at runtime.
3. **Build an entity gazetteer from the log itself.** A capitalised token
   that recurs (say ≥3 times) is a character or place and is worth treating
   as payload; one that appears once is probably just a sentence-initial word
   or incidental. This replaces "any capital = payload" with something that
   earns its keep, and should cut hard into the 33.7%.
4. **Extract the clause, not the first 120 characters.** When a sentence
   exceeds the cap, keep the clause containing the payload rather than
   truncating — design rule 2 applied to long prose.
5. **Speaker-aware subjects.** SillyTavern gives `name` and `is_user` per
   message; use them as the knot subject instead of always "the user". Group
   chats then get one subject per character, which also makes supersession
   safer: "Alice is at the tavern" and "Bob is at the tavern" stop sharing a
   frame.
6. **Supersede without deleting.** RP state changes constantly (location,
   mood, clothing, who knows what). Superseded knots should be *untied* and
   kept as history — the model the `/qontext` skill already uses — not
   dropped.

## Recall baseline — and it overturns the plan above

An answer key now exists for log13 (`rp_questions.py`): 20 questions about the
things a memory layer should carry across a long session — names, the state of
the relationship, what changed, what was agreed. Measured with `rp_recall.py`:

| | |
|---|---|
| recalled at 300 chars | 7/20 |
| recalled at 600 chars | 8/20 |
| recalled at 1200 chars | 8/20 |
| **facts present in memory** | **18/20** |

Splitting those: **2 extraction failures, 10 retrieval failures.** The facts
are in there. The pack cannot find them.

That inverts the six rules above. They are all extraction fixes, and
extraction is not the bottleneck on real logs — it is worth roughly 2 of 20.
The prize is retrieval at scale: 498 knots of prose, all sharing the same
vocabulary, where scoring tuned on 10–58 knot memories stops discriminating.
Note also that recall barely moves from 300 to 1200 characters, which is the
signature of a ranking failure rather than a capacity one.

Where retrieval likely breaks:
- **Question words are generic in fiction.** "What is the vampire's name?"
  matches every knot containing "name" or "called", and prose is full of them.
- **IDF was computed over tiny memories.** With 500 knots the document
  frequencies are completely different, and the constants were never tuned there.
- **Knots are long.** 68 chars average, so a 300-char budget holds four of
  them; a wrong pick costs a quarter of the pack.

## Correction: the logs *do* have speakers, and my parser threw them away

The claim above that these logs carry no attribution was **wrong**. Turns are
separated by a bare `Preview` line followed by the speaker's name; log13 is 75
User turns and 75 Carmine turns, not 785 anonymous paragraphs. `load_text`
flattened every paragraph into one unattributed turn, so every character's
first-person narration was rewritten to "the user" — which is precisely why
Carmine's own name was almost absent from her memory, and why retrieval could
not find her.

Two things follow. Per-turn statistics in the first table are off by roughly
5x (density and knot counts are unaffected, being per-character). And rule 5
— speaker-aware subjects — moved from "cannot be validated offline" to
"validated offline, and necessary".

## Where it stands after the retrieval round

`speakers="all"` on `QontextMemory` makes each speaker their own subject:
"I'm a vampire" from Carmine becomes "Carmine is a vampire". Combined with
the parser fix and an adaptive relevance floor (dropped above 200 knots,
where it was cutting off answers along with noise):

| | before | after |
|---|---|---|
| log13 recall @600 | 8/20 | **10/20** |
| log13 recall @1200 | 8/20 | **11/20** |
| facts in memory | 18/20 | 18/20 |
| A / B / C@300 | 10/10, 10/10, 40/40 | unchanged |
| C noise @300 | 14% | 14% |

Still 9 retrieval failures against 18 facts present.

**Entity bridge: built, measured, reverted.** The hypothesis was that fiction
never declares a name ("my name is Carmine") but only uses it, so a query
should be expanded with the entities its words co-occur with — "vampire" →
Carmine. It works: entity detection found carmine and chance from the
capitalised-token counts, and the bridge fired correctly for
`{vampire, name}`. Recall did not move, at any linking threshold from 0.5
down to 0.1.

The reason is worth keeping: **speaker-aware extraction had already solved
it.** Once Carmine's own narration is attributed to Carmine, her name is
scattered through hundreds of knots, so the bridge points at something the
query could already reach. The idea may still earn its place when the entity
in question is talked *about* rather than speaking — a third character, a
place, an off-screen event — but nothing in this corpus demonstrates that, so
it is out.

## Importance — computed, not rated

Knots now carry a 1–5 weight, the same scale the `.qx` weave format uses.
Nothing rates them by hand and no model is called; the weight comes from
signals the memory already holds:

- **what kind of fact it is** — identity and relationships (5) outrank
  standing preferences and circumstances (4), commitments (3), working
  furniture (2), everything else (1);
- **whether it carries a payload** — a category word with nothing concrete
  attached loses half a point;
- **whether the speaker flagged it** — "remember", "promise", "important"
  adds a point, because being told a thing matters beats guessing;
- **reinforcement** — restatements (counted when supersession fires) and
  retrievals add up to one more point, on a log curve so a fact mentioned
  twice never outranks an identity.

**It governs eviction only, and gently.** Three versions were measured:

| variant | flood test | RP @cap 200 | C@300 |
|---|---|---|---|
| baseline (no importance) | 40/40 | 6/20 | 40/40 |
| importance ordered first | 28/40 | 8/20 | 40/40 |
| × raw 1–5 weight | 30/40 | 8/20 | 40/40 |
| **× (1 + weight/5)** | **40/40** | **9/20** | **40/40** |

The two strong versions failed the same way: chatter that happens to say
"meeting" inherits a commitment's weight and survives, while "Docs are in
Notion" — distinctive, but in no importance category — gets evicted. Rarity
is what separates a fact from filler; importance only says what that fact is
worth once it has qualified. Hence the gentle multiplier.

Letting importance sway *retrieval* was also measured and rejected: at 0.5 it
bought one fact on the roleplay log and cost three on conversation C. An
important knot is not the same thing as a relevant one. The knob
(`IMPORTANCE_RANK`) exists and defaults to off.

The nine that remain are single-mention details — a surname said once, what
is on a wall, what was cancelled. Those are a coverage problem, not a ranking
one: with 481 knots and a 300-character budget, a fact mentioned once in 150
turns is competing with everything else that was ever said.

## The quiz benchmark was flattering by a factor of twenty

`rp_turnbench.py` measures the shape the extension will actually experience:
at each user turn, build the pack **from that turn's message**, from a memory
holding only earlier turns, then check whether the facts the character's next
reply drew on were carried. Ground truth comes from the conversation itself —
a rare word appearing in the reply, established earlier, and *not* available
from the last six messages, since memory only has to supply what verbatim
recency cannot.

| benchmark | query shape | score |
|---|---|---|
| `rp_recall.py` | "What is the vampire's name?" | 10/20 (50%) |
| `rp_turnbench.py` | "I lean over and kiss her cheek" | **2.0% @300, 5.4% @1200** |

Both are measuring the same memory. The difference is entirely in the query.
A question shares vocabulary with its answer; a roleplay turn does not, and
keyword retrieval has nothing to work with.

**Budget is not the constraint either.** Quiz recall plateaus at 12/20 by
2400 characters and stays there at 9600 — 104 knots packed, a fifth of the
whole memory — against a ceiling of 18/20. Six facts are simply invisible to
the scorer: "What did Carmine cancel?" has query words {cancel, morning}, and
the knot holding the answer says "didn't you have class though?". No shared
word, so no threshold and no budget can connect them.

## Reserved slice: importance instead of the query

`PACK_RESERVE` fills part of the pack by importance, ignoring the query
entirely — the standing facts a character needs in every scene, which lose
every relevance contest because nothing in the turn mentions them.

| reserve | turnbench@1200 | quiz@600 | A | B | C@150 | C@300 | flood |
|---|---|---|---|---|---|---|---|
| 0.00 | 5.4% | 10/20 | 10/10 | 10/10 | 37/40 | 40/40 | 40/40 |
| 0.25 | 7.0% | 12/20 | 10/10 | 10/10 | 36/40 | 39/40 | 39/40 |
| 0.50 | **9.3%** | 12/20 | 10/10 | 10/10 | 29/40 | 37/40 | 37/40 |
| 0.75 | 9.6% | 11/20 | 10/10 | 10/10 | 16/40 | 33/40 | 33/40 |

It nearly doubles the real metric and costs the chat suites, which is exactly
what it should do: reserving space is a bet that the query is uninformative.
In assistant chat that bet is wrong, in roleplay it is right. **Default 0.0,
set 0.5 for roleplay** — the one constant here that genuinely depends on
deployment rather than on tuning.

And the honest headline: 9.3% is nearly double 5.4%, and both are small. The
turn-shaped task is mostly unsolved, and now it is measured rather than
assumed.

## The vocabulary weave: mechanism proven, data starved

`qontext_weave.py` is a second cord one level below the facts — a persistent
map of which words hang together, built by counting co-occurrence and scored
with normalised PMI. The idea: a memory can only retrieve what the query's
words reach, and a weave that accumulates across sessions is outside
knowledge relative to any one conversation, without a model or a dependency.

**The mechanism works.** Fed a dozen sentences where "cancel", "class" and
"morning" co-occur, the query *"what did she cancel?"* retrieves *"the user
has a class at the university on Tuesday"* — a knot sharing no word with the
question. That is the exact bridge that was declared impossible three rounds
ago, working.

**The cross-log test says it needs far more text than a person produces.**
A weave built from nine other logs, tested on log13 which contributed nothing
to it:

| setting | turnbench @600 | @1200 |
|---|---|---|
| no weave | 3.4% | 5.4% |
| weave, top 6 threads ≥0.20 | 2.8% | 4.5% |
| weave, top 2 threads ≥0.50 | 2.8% | 5.1% |
| weave, top 1 thread ≥0.70 | 3.4% | 5.4% |
| weave, association weight 0.25 | 3.1% | 4.8% |

Every setting is neutral or worse, and it only stops hurting when filtered so
hard that almost nothing passes. The associations are mostly noise, because
there is nothing like enough text:

| logs learned | tokens | woven words | probe words covered |
|---|---|---|---|
| 1 | 4,506 | 146 | 2/9 |
| 3 | 12,311 | 357 | 3/9 |
| 6 | 21,089 | 774 | 3/9 |
| 9 | 29,545 | 1,086 | 4/9 |

Growth is roughly linear in tokens: 30k tokens buys 1,086 words, and
"cancel", "class" and "husband" still have no threads at all. Word vectors
are trained on billions of tokens for a reason. A personal corpus reaches
useful density after millions of tokens — years of conversation, or one
seeding pass over a public corpus, which the design supports since `learn()`
will count any text you hand it.

**Kept, off by default.** `QontextMemory(weave=...)` is wired and tested; the
default is `None`.

### Seeded from WikiText-103: the tank was filled, and it still didn't fly

43.9M tokens, 40,327 words woven, 10.2 MB, ~7 minutes to build with
`seed_weave.py`. The associations are visibly real:

```
vampire  -> buffy 0.75, slayer 0.71, masquerade 0.66, dracula 0.61
allergic -> reactions 0.61, anaphylaxis 0.60, asthma 0.57, allergies 0.55
husband  -> householder 0.64, couples 0.48, wife 0.48, divorce 0.47
```

And visibly captured by the corpus's own obsessions:

```
class    -> battleships 0.63, mackensen 0.54, dreadnought 0.53, cruisers 0.51
cancel   -> scheer 0.57, hipper 0.56, unrest 0.54, subscriptions 0.46
```

WikiText-103 is thick with naval history, so both halves of the very bridge
this was built for — "cancel" and "class" — resolved to battleship orders.

Results, against every suite:

| | no weave | WikiText-103 weave |
|---|---|---|
| turnbench @300 | 2.0% | 2.0% |
| turnbench @600 | 3.4% | 3.7% |
| turnbench @1200 | 5.4% | 5.4% |
| quiz @600 | 10/20 | 9/20 |
| quiz @1200 | 11/20 | 11/20 |
| A / B / C@150 / C@300 | 10, 10, 37, 40 | identical |

One extra fact out of 355 at one budget, one fewer on the quiz. That is
noise. Seeding did fix the *harm* — the unseeded weave scored 2.8% and 4.5%,
measurably worse than nothing — so more text made a bad component neutral
rather than good.

**Verdict: the idea works and does not pay.** The mechanism is real (a
hand-fed weave demonstrably retrieves a knot sharing no word with the query),
the scale problem was solved, and the result is still nothing. Three
explanations, in the order I would test them:

1. **Wrong domain.** Encyclopedia prose is a poor model of how people talk.
   Gutenberg narrative would match roleplay far better, and is the one cheap
   experiment left.
2. **Wrong unit.** Association operates on single words, but what the reply
   needs is a *fact*. Expanding "kiss" to "cheek, lips, forehead" does not
   help find "Carmine cancelled her morning class".
3. **Wrong problem.** At 481 knots and a 300-character budget, the pack holds
   six. Even perfect query understanding cannot carry five needed facts in
   six slots — the ceiling may simply be low, and the honest fix is a bigger
   pack or fewer, better knots rather than better retrieval.

Explanation 3 is the one I would bet on now, and it points back at extraction
— 3,365 knots from 12 logs, a third of them structurally broken — rather than
at anything clever.

## Extraction repair — the unglamorous fix that actually paid

Three changes, in the order they had to happen:

1. **Quotations are atomic.** Sentence splitting and clause trimming both
   skip any punctuation inside a quotation. Prose puts full stops inside
   quotes constantly, and cutting there leaves the payload on the other side
   of the break.
2. **Fit, don't truncate.** A sentence over the 120-character cap is no
   longer sliced at 120. It is split into clauses (outside quotes) and the
   longest span that *carries the payload* and fits is kept. Slicing was a
   direct violation of design rule 2, and the fix only became urgent once
   quote-aware splitting made sentences longer.
3. **Attribute the dialogue.** A knot that is a bare quoted line becomes
   `Carmine says "…"` — using the speaker that `speakers="all"` already
   supplies. An utterance nobody is saying is not a fact.

| defect | original | quote-aware | + clause & attribution |
|---|---|---|---|
| broken mid-quote | 17.7% | 8.5% | **7.8%** |
| truncated at the cap | 18.3% | 26.8% | **6.7%** |
| bare dialogue | 16.5% | 27.9% | **0.0%** |

The middle column is the instructive one: fixing the split alone made two of
the three metrics *worse*, because intact sentences are longer and hit the
cap. The three changes only work together.

Retrieval, measured after:

| | before | after |
|---|---|---|
| turnbench @1200 | 5.4% | **7.1%** |
| turnbench @600 | 3.4% | 3.4% |
| turnbench @300 | 2.0% | 1.4% |
| quiz @300 | 7/20 | **9/20** |
| quiz @1200 | 11/20 | 9/20 |
| A / B / C@150 / C@300 | 10, 10, 37, 40 | unchanged |

Better where there is room to work (+1.7 points at 1200, +2 on the quiz at
300), slightly worse at the tightest budgets, where longer intact knots cost
slots. Knot count fell 2422 → 2253 and mean length rose 71 → 76: fewer,
longer, less broken. Which is design rule 3, arrived at from the other
direction.

**This was worth more than the weave, the entity bridge and the golden-ratio
sweep combined**, and it was sitting in the first probe I ran.

## What this corpus cannot tell us

- **Speaker rules.** No labels, so rules 2 and 5 have to be designed against
  SillyTavern's data model and validated in the extension, not here.
- **Recall.** There is no answer key. Precision categories above are
  measurable, but "did the pack contain the fact the model needed" is not,
  until questions are written for a couple of logs by hand.


# The RP core: qontext_rp.py, and the finding that reverses this document

`RPMemory` is the roleplay build the SillyTavern extension will be a port of.
Measured with `rp_turnbench.py --engine rp` over 11 logs (log8 and log12
excluded in code, not by convention), turn-shaped queries, ground truth taken
from what each character's next reply actually drew on:

| budget | chat `pack()` | RP `scene()` |
|---|---|---|
| 300 | 2.1% | **3.4%** |
| 600 | 2.7% | **5.9%** |
| 1200 | 4.1% | **10.4%** |

## Extraction was the bottleneck after all

This document previously concluded the opposite: on the quiz benchmark, 18 of
20 facts were present and only 2 were extraction failures, so extraction was
declared worth little and retrieval declared the prize. The ablation says that
was true of the quiz and false of the task:

| variant, budget 1200 | mean across 11 logs |
|---|---|
| chat `pack()` | 4.1% |
| RP extraction only, no reserve, no cords | **6.3%** |
| RP extraction + cords | 8.5% |
| RP extraction + reserved slice | 9.6% |
| RP extraction + reserve + cords | **9.9%** |

**All three contribute.** Extraction alone is worth 2.2 points over the chat
build, the reserved slice another 3.3, and the cords 2.2 on their own but only
0.3 once the reserve is present — the two overlap, because both spend budget
on knots the query never asked for. The quiz measured whether a fact was
*somewhere* in a 500-knot memory; the turn benchmark measures whether it
reaches a 1,200-character pack built from a sentence like "I lean over and
kiss her cheek". A knot that exists but is malformed passes the first test and
fails the second.

### What the chat rule was throwing away

The chat extractor admits a sentence on a marker or a "payload" — a number, a
date, a hyphenated coinage, a mid-sentence capital. Roleplay's most important
sentences have none of those. Observed directly:

```
"I am at the harbour now, not the shrine. Plans changed."
  chat extractor -> ['Plans changed']
  RP extractor   -> ['Carmine is at the harbour now, not the shrine',
                     'Plans changed']
```

The location change — the single most consequential kind of statement in a
session — was silently dropped, and the knot that survived is the one that
says nothing. `rp_is_fact` therefore treats **state as payload**, recognised
structurally rather than by vocabulary, since the nouns of a setting cannot be
known in advance: place (`at the harbour`), possession (`wearing the grey
coat`), kinship, condition, and commitment.

## Cords: the first ablation was broken

The first version of this section reported that cords *hurt* — 9.2% without
them against 8.5% with. That was an artifact of the experiment, not a result.

`CORD_SHARE = 0.0` was used to mean "cords off". It does not. It gives the
query-driven seeds the entire non-reserved slice, after which the cord walk
still runs and still fills whatever the seeds left over. Both arms of that
ablation had cords in them; the only thing being varied was the budget split.

With a real switch (`use_cords`), the sign reverses: **6.3% without cords,
8.5% with**, at reserve 0. The mechanism is the one the design predicted — a
roleplay turn usually lands on *something*, a name or a place, and the knots
hanging off it are the scene.

This is the fourth time in this project that a measurement, rather than the
thing measured, produced the finding. The pattern is consistent enough to be
worth stating as a rule: **a knob that is supposed to disable a feature must
be tested by confirming the feature stopped happening, not by assuming the
knob means what its name says.**

Defaults: `USE_CORDS = True`, `CORD_SHARE = 0.4`, `SCENE_RESERVE = 0.5`.

## Honest ceiling

10.4% is a doubling of 4.1% and it is still one fact in ten. The turn-shaped
roleplay task remains mostly unsolved, and no amount of extraction repair will
finish it — a query that shares no vocabulary with its answer is not a ranking
problem. What changed here is that the memory now *contains* the right things
and states them in a retrievable form; what has not changed is that a roleplay
turn is a poor query.


# Half the memory was not facts

Found by measuring what a pack actually contained, after real dialogue
replaced synthetic filler. Of 259 knots extracted from an 800-turn
DailyDialog conversation:

| category | share of knots |
|---|---|
| starts as a question | 24% |
| addresses "you" | 20% |
| imperative | 3% |
| **combined** | **47%** — and 50% of stored characters |

```
Do you think you'll ever get another pet
What was the party like last night, Jean
Don't believe what you see on TV
can the user uses your laptop for a while
```

None of those are claims about anybody. The extractor was built for a user
*stating* things, and every benchmark conversation before DailyDialog
consisted only of statements — so this was invisible for the entire project
and had been eating half the memory the whole time.

## The filter, and the trap in it

Two rules, in `extract()`:

1. **A question is not a fact, whoever asks it.** Checked on the raw sentence,
   because the opener strip removes the question mark along with the rest of
   the punctuation and by then the evidence is gone.
2. **A remark aimed at the other party is not a fact about the speaker** —
   unless it also names the speaker. "You will have a good time in New York"
   goes; "I told you about my sister Vesna" stays.

Imperatives are deliberately *not* filtered, and the reason is in the junk
list itself:

```
Please keep explanations brief
```

That is one of the benchmark's ten planted facts. Preferences are imperative
in form and factual in content, so a naive "drop commands" rule deletes real
answers. Two rules that are cheap to verify beat a third that needs judgement.

Rule 2 is off for roleplay (`drop_address=False`). In assistant chat the other
party is the assistant, which has no facts of its own; in a scene it is a
character, and "you are the vampire's sire" is exactly what the memory exists
to keep.

## Measured, everything

| | before | after |
|---|---|---|
| unit tests | 91 pass | **98 pass** |
| suite A / B recall | 10/10, 10/10 | unchanged |
| suite C @150 / @300 | 37/40, 40/40 | unchanged |
| suite C noise @300 | 12% | unchanged |
| supersede corrections / safety / invariant | 10/11, 27/27, 0 | unchanged |
| knots stored (real filler, 3 seeds) | 749 / 770 / 692 | **451 / 476 / 454** |
| junk characters | 45% / 41% / 39% | **20% / 13% / 14%** |
| containment (0/10/30% decoys) | 30/30, 28/30, 29/30 | 30/30, **29/30**, 29/30 |
| RP turnbench @300/@600/@1200 | 3.4% / 5.9% / 10.4% | 3.4% / 5.7% / 10.4% |

**Forty percent fewer knots stored, junk more than halved, and containment
slightly better.** Every chat suite is untouched, which is the point: the
filter removes things that were never facts, so nothing that was working
should move — and nothing did.

The residual 13-20% overstates what is left; the measuring regex is cruder
than the filter and counts knots that legitimately contain "you" alongside a
first-person subject.

## Where it came from

A suggestion that stenography might be worth borrowing from — chorded strokes
carrying several units at once. Applied to knots, the analogue was to stop
repeating the subject across knots about the same person. Measured, that is
worth **2.4%** of pack budget and is not worth doing.

But the measurement printed a pack, and the pack was full of questions. The
idea was wrong and the test it motivated found something twenty times larger.


# Reachable or invisible: the decomposition that reframes the problem

Aggregate recall hides the mechanism. Splitting every missed fact by *why* it
was missed turns out to change what should be built next.

For each fact the next reply demonstrably needed, and which recency could not
have supplied, ask where it was when the pack was built (5 logs, budget 1200,
`diagnose_misses.py`):

| | count | share |
|---|---|---|
| carried by the pack | 30 | 5.3% |
| ranked, but outbid for budget | 85 | **14.9%** |
| unreachable — score exactly zero | 454 | **79.8%** |

**Four in five misses never become candidates at all.** The knot shares no
word with the query, scores zero, and ranking never sees it. Of the minority
that were reachable and lost, the median sat at rank 66 — not narrowly beaten,
buried.

This kills a plausible research direction. Better *discrimination* — richer
confidence signals, score-gap triggers, entropy of the candidate distribution
— can address at most the 15%. It cannot be the main event on this workload,
however elegant.

## The bottleneck depends on query shape, not on the memory

The same memory, the same logs, two query shapes:

| query shape | example | recall |
|---|---|---|
| quiz | "What is the vampire's name?" | 18/20 facts present, 10-12 retrievable |
| turn | "I lean over and kiss her cheek" | 80% of needed facts unreachable |

A question repeats the vocabulary of its answer. A conversational turn refers
to a discourse entity instead of describing it — "didn't she cancel that?"
names neither the class nor the cancellation — and lexical retrieval has
nothing to work with. **These are two different retrieval problems wearing the
same interface.**

## What a bridge has to clear, measured on the bridge we have

The right question for a semantic component is not "does accuracy improve".
It is: *of the facts that are unreachable, how many does it reach, and what
does that cost in candidate inflation?* Overall accuracy cannot separate a
bridge that reaches nothing from one that reaches plenty and fuses badly.

`bridge_ceiling.py`, on the same 454:

| | |
|---|---|
| made reachable by the WikiText-103 weave | **31 (6.8%)** |
| of those, actually reached the pack | 4 (0.9%) |
| candidate pool per turn | 40 → 49 (1.2x) |

**The weave's ceiling is 6.8%.** Even with perfect fusion and a perfect gate,
93% of unreachable facts stay unreachable. The +2 net facts measured earlier
was never a fusion problem.

### Correction to the earlier diagnosis in this document

An earlier section attributed the weave's failure to indiscriminate expansion
flooding the candidate pool — 47 association tokens per turn. The pool grows
by **1.2x**, so that was wrong. The expansion is wide in vocabulary and narrow
in effect: those 47 tokens mostly expand to words that appear in no stored
knot at all.

The reason is the *direction* of the association. PPMI learns
`class → teacher, lesson, school` — words that share a situation. The hop
actually required is `cancelled → planned activity → class`, which is
reference resolution, not co-occurrence. Counting will not produce it at any
corpus size.

## The bar for the next experiment

Any semantic retriever should be judged first on one number:

> **What fraction of the 454 unreachable facts does it make reachable,
> without touching supersession?**

Above roughly 40%, bridges are the right category and fusion becomes worth
engineering. Around 10%, discourse-shaped reference is not a retrieval problem
and the search should move elsewhere. The weave sits at 6.8%, which is the
number to beat.

Supersession stays symbolic regardless. "Alice is at the tavern" and "Bob is
at the tavern" are near-identical in any embedding space and must never merge;
the 27/27 safety suite exists to catch exactly that, and cosine similarity
would walk straight through it.

## The bar, tested: embeddings tie the weave at matched cost

`embed_bridge.py`, same 454 unreachable facts, same protocol. A bridge
proposes K entry points per query, so K is also its price — the candidate pool
grows by K whether or not any of them is right.

| bridge | reached | cost per turn |
|---|---|---|
| WikiText-103 weave, ~6 terms | 31 (6.8%) | +9 candidates |
| embeddings, top-5 | 15 (3.3%) | +5 |
| embeddings, top-10 | 34 (**7.5%**) | +10 |
| embeddings, top-20 | 78 (17.2%) | +20 |
| embeddings, top-50 | 201 (**44.3%**) | +50 |

**At matched cost the embedding bridge ties the co-occurrence weave**: 7.5%
against 6.8% for the same ten extra candidates. The 40% target only arrives at
top-50, which more than doubles a lexical pool averaging 40 knots per turn.

The signal is real rather than noise — top-20 reaches 17.2% where random
selection from ~500 knots would give roughly 4%, so about four times chance.
It simply does not *concentrate*. The right fact is somewhere in the top fifty
and rarely in the top ten.

The motivating probe still holds, which is what makes this interesting rather
than merely negative:

```
"didn't she cancel that?"  vs  "didn't you have class though?"   0.352
"didn't she cancel that?"  vs  "the user is at the harbour"      0.042
```

An order of magnitude apart. The mechanism exists. It is the *ranking* of that
mechanism across five hundred candidates that fails to isolate the answer.

### Three things this does not establish

**It is a lower bound.** `potion-base-8M` is a static distilled model — no
context window, effectively a well-trained bag of word vectors. A contextual
sentence encoder should score higher, and testing one is the obvious next
step: change `MODEL` and swap `StaticModel.from_pretrained` for
`SentenceTransformer`.

**Reach is not retrieval.** This counts whether a fact enters the top-K, not
whether it survives ranking and packing afterwards. The weave lost six of
every seven facts it rescued at that stage, and there is no reason to assume
embeddings would do better.

**One workload.** Five roleplay logs, turn-shaped queries. On quiz-shaped
questions reachability was never the problem.

### Where that leaves the design

Neither bridge earns a place in the default path on this evidence. Both remain
optional and off. What has changed is that the question is now precise and
cheap to re-ask: any future retriever gets pointed at `bridge_ceiling.py` and
`embed_bridge.py` and has to beat **6.8% at +10 candidates**, on the
population that actually needs rescuing, before anyone argues about fusion.

That is the useful residue of a negative result — not "semantics did not
help", but a protocol, a population, and a number.

### Contextual embeddings do not beat a bag of word vectors

The static result was published as a lower bound, on the reasoning that a real
sentence encoder should do better. It does not.

`all-MiniLM-L6-v2`, same 454 facts, same protocol, run on the author's
machine:

| K | potion-base-8M (static) | all-MiniLM-L6-v2 (contextual) |
|---|---|---|
| 5 | 3.3% | 4.4% |
| **10** | **7.5%** | **7.7%** |
| 20 | 17.2% | 18.3% |
| 50 | 44.3% | 47.6% |

**Two-tenths of a point at matched cost.** A model with a context window,
trained on sentence pairs, scores the same as one that averages static word
vectors — and both sit within a point of a co-occurrence count over
WikiText-103.

Three methods that share nothing in their construction, landing in the same
place, is not a sequence of implementation failures. **The ceiling is in the
formulation.**

The reason is visible once stated. All three ask the same question: *which
stored fact is most similar to this turn?* But a roleplay turn is similar to
dozens of scene knots at once, and the fact the reply needs is rarely the most
similar one. The relation required is *"which fact does what I am about to say
depend on"* — relevance to an unwritten continuation — and that is not
similarity between two strings. No encoder computes it because it is not a
property of the pair.

### Verdict, against a threshold set before the measurement

Written down before the run: above roughly 25-30% at top-10, bridges are worth
engineering; near 9%, the category is a dead end for this task. The result is
**7.7%**.

So: **semantic retrieval is not the answer to discourse-shaped reference**, and
this project has now spent three separate mechanisms establishing it. The
weave stays off. No embedding dependency is added. `qontext_memory.py` remains
stdlib-only, and that is now an evidenced decision rather than an aesthetic
preference.

What remains unsolved is stated plainly rather than hidden: on turn-shaped
queries, roughly 80% of the facts a reply needs are unreachable, and nothing
tried so far reaches them affordably. The reserved slice (`PACK_RESERVE`) is
the only thing that has ever helped, and it works by *ignoring the query
entirely* — which, in hindsight, is the same insight arriving from the other
direction: if the turn is a poor question, stop asking it.


# The information was there at write time, and extraction threw it away

Three retrieval bridges converge at ~7%. The obvious redirect is to fix it
earlier — store a representation that carries the bridge, so no clever
retriever is needed. That relocates the problem rather than dissolving it,
since inferring "cancelled" from "didn't you have class though?" needs the
same discourse understanding the retriever lacked.

But it relocates it somewhere *better*, and the reason is an asymmetry worth
stating plainly: **the surrounding turns exist when the knot is written and
are gone when the question is asked.** Whether that helps is measurable
without any model — `write_time_oracle.py` asks whether the future query
shares a content word with the neighbourhood of the turn that produced the
knot.

| context around the knot's source turn | query shares a word |
|---|---|
| the source turn only | **202 / 454 (44.5%)** |
| ± 1 turn | 252 (55.5%) |
| ± 2 turns | 324 (71.4%) |
| ± 4 turns | 379 (83.5%) |

**In 44.5% of unreachable cases the bridging word was in the very turn the
knot came from.** Not latent, not inferred — present in the text, at the
moment of writing, and discarded by extraction. Against 7.7% for the best
retrieval bridge, that is a six-fold larger population.

It also explains a pattern nobody had joined up. Every improvement this
project ever measured as a win was an extraction change: state-as-payload
(4.1% → 6.3%), speaker-aware subjects (8/20 → 10/20), the question filter
(40% fewer knots, junk halved). Retrieval changes have produced ties or
losses. We kept finding gains in extraction and treating them as incidental
housekeeping.

## The tension this creates, which is real

The obvious fix is to keep more of the source turn's vocabulary in the knot.
Design rules 2 and 3 say the opposite — *the payload is the point*, *fewer,
better knots* — and they were each paid for with a failed run. Fatten every
knot with context words and the pack spends budget on vocabulary that exists
only to be searchable, which is exactly the noise the density rules prevent.

The resolution is that these are two different jobs wearing one field:

> **Index terms are not display text.**

A knot can carry a hidden term set — distinctive words from its source turn —
used only for matching and never sent to the model. The pack stays a hundred
tokens of clean third-person facts; reachability comes from words nobody has
to read. Storage grows, the prompt does not.

That is the first candidate in this whole line of work that needs no
dependency, targets the 44.5% population rather than the 7.7% one, and does
not trade against the design rules.

## Built it. It does not work either, and the reason matters.

`INDEX_TERMS` adds hidden matching terms to every knot: the most distinctive
words from its source turn, indexed for retrieval and never rendered into the
pack. Rarest-first by document frequency, discounted at `INDEX_WEIGHT` when
scoring, excluded from supersession and from the frame.

| terms per knot | turnbench @300 | @1200 |
|---|---|---|
| 0 (off) | 1.8% | 3.8% |
| 3 | 1.8% | 3.5% |
| 6 | 1.7% | 3.4% |
| 10 | 2.2% | 4.3% |
| 16 | 1.9% | 3.9% |

Chat suites A, B and C are unchanged at every setting, so it costs nothing.
It also buys nothing: worse than off at 3 and 6, better at 10, back down at
16. Non-monotonic across four settings is the shape of noise, not of a
mechanism with an optimum.

### Why the 44.5% did not convert

The oracle measured **availability** — was a bridging word present near the
knot when it was written. It did not measure **discrimination**, and those
come apart completely, because *every* knot gets index terms. The same words
that make the right knot reachable make dozens of wrong ones reachable at the
same time. Reachability is not scarce once you are willing to index anything;
what is scarce is a reason to prefer one reachable knot over another.

That is precisely the weave's failure arriving from the opposite direction.
The weave broadened the query; index terms broaden the knots. Both raise
recall of a pool that was never the constraint, and neither improves the
choice among what is retrieved.

**Kept, off by default (`INDEX_TERMS = 0`).** The mechanism is sound, tested,
costs nothing when disabled, and is there for anyone who finds a workload
where reachability genuinely is the binding constraint. On turn-shaped
roleplay retrieval it is not.

### What five negative results in a row actually establish

PPMI expansion, static embeddings, contextual embeddings, cord expansion, and
now write-time index terms. Five mechanisms, three of them semantic, two of
them structural, all landing within noise of the baseline.

The one thing that has ever moved this metric is `PACK_RESERVE` — filling half
the pack by importance and **ignoring the query entirely**. That is not a
retrieval improvement. It is a decision to stop treating the turn as a query.

Read together, the evidence says: for turn-shaped input, the question "which
stored fact is relevant to this text?" may not be answerable from the text.
Every attempt to answer it better has failed identically. The approach that
works declines to ask.

## Correction: it does work, and the aggregate was hiding it

The section above concluded the mechanism was noise. That was measured with
rarest-first term selection and read only as an 11-log mean, and both were
mistakes.

**A document-frequency ceiling, not merely rarest-first.** A word the memory
has already seen in a large share of its knots is background, not an entry
point — indexing it makes every knot reachable by a word that identifies none
of them. Skipping any term already present in more than 2% of knots:

| chat engine, budget 300 | |
|---|---|
| off | 1.8% |
| rarest-first | 2.2% |
| **df ceiling** | **2.5%** |

**And the per-log view, which the mean concealed.** Six of eleven logs are
flat — three score 0.0% under every setting — so a real gain on five logs
becomes a small aggregate.

| | better | worse | unchanged |
|---|---|---|---|
| chat engine @300 | **5** | **0** | 6 |
| RP engine @1200 | **6** | 2 | 3 |

Five better and none worse is a sign test at p ≈ 0.03. This is the first
mechanism in the whole retrieval line that improves without ever harming.

**On the deployment path it was inert until wired.** `RPMemory` has its own
extraction and was calling `_add(knot)` without the source turn, so index
terms never populated in the build that actually ships. With the context
passed:

| RP engine | before | after |
|---|---|---|
| budget 300 | 3.4% | **3.6%** |
| budget 600 | 5.7% | 5.7% |
| budget 1200 | 10.4% | **11.2%** |

Chat suites A and B unchanged at 10/10; C recall unchanged with noise 12% → 13%
at budget 300. Supersession untouched — 27/27 pairs kept apart, 0 collapses —
because index terms are excluded from the frame and from the similarity check
by construction. 98 tests green.

**Enabled by default**: `INDEX_TERMS = 10`, `INDEX_DF_CEILING = 0.02`,
`INDEX_WEIGHT = 0.35`.

### The idea, named properly

Not "hidden index terms" — that is the implementation. The finding is:

> **A memory entry needs two representations, because reasoning and retrieval
> are different objectives.**
>
> The *payload* is optimised for the model: dense, readable, self-contained,
> cheap in the pack. The *index* is optimised for being found: reachable,
> discriminative, and never rendered.

Most memory systems use one representation for both, which is what created the
tension with design rules 2 and 3 here — every attempt to make knots more
findable made them worse to read, and every attempt to make them denser made
them harder to find. Separating the objectives dissolves the conflict, and the
measurements are what forced the separation rather than taste.

The gain is modest — one point at budget 1200 — and the honest framing is that
it is the first *directionally clean* result after five that were not.


# The arc, and what it does not establish

| hypothesis | prediction | result | conclusion |
|---|---|---|---|
| tuning fixes retrieval | stronger constants raise recall | flat response surface | not the bottleneck |
| statistical bridge | rescues many zero-score facts | 6.8% ceiling | wrong bridge |
| contextual embeddings | ≥25-30% rescue | 7.7% | similarity is not the missing information |
| preserve source vocabulary | large oracle | 44.5% available | extraction is where information is lost |
| dual representation + DF ceiling | recover some of it cheaply | +0.8 pts, 5-0 per-log | architecture, not retrieval |

Five hypotheses, four eliminated, each by a measurement designed to be capable
of killing it.

## The caveats, stated at the same volume as the claim

**The gain is one point.** RP turnbench at budget 1200: 10.4% → 11.2%. The
sign test is clean (5 better, 0 worse on the chat engine) but n is eleven logs
with six of them flat. This is a directional result, not an effect size anyone
should quote.

**It captures a small share of what the oracle promised.** The oracle said the
bridging word was available in 44.5% of unreachable cases. The implementation
converts roughly one point of the ~7 available at that budget. Either the
mechanism is weak or the oracle overstates what "available" is worth — and the
whole point of the earlier `INDEX_TERMS` failure was that those are different
things. Availability is not usability, and we have now demonstrated that twice.

**It has never been near a model.** Every number in this section is
containment: did the knot enter the pack. Whether a model answers better with
it is unmeasured.

**One extraction pipeline, one corpus, one machine.** The principle is stated
as though it generalises. Nothing here shows that it does.

## The falsifiable version

If the dual-representation account is right rather than a lucky constant, it
predicts something specific: **the benefit should grow with memory size.** A
document-frequency ceiling can only matter once there are enough knots for
background words to become background — in a 20-knot memory nothing is common,
so the ceiling never binds and index terms should behave like rarest-first.

That is a real prediction and it could fail. If the gain is flat across memory
sizes, the mechanism is not what this section says it is, and the honest
conclusion becomes "a constant that happened to help on eleven logs".

Written down before measuring, so it cannot be reinterpreted afterwards.

## The falsification test was underpowered, and that is the result

`size_scaling.py` truncates each log to a prefix and replays it, comparing
index terms on and off at several memory sizes.

| turns | knots/log | needed facts | off | on | gap |
|---|---|---|---|---|---|
| 60 | 157 | 777 | 4.9% | 5.4% | +0.5 |
| 150 | 204 | 1,284 | 5.4% | 5.6% | +0.2 |
| 300 | 204 | 1,289 | 5.4% | 5.6% | +0.2 |
| 600 | 204 | 1,289 | 5.4% | 5.6% | +0.2 |
| all | 204 | 1,289 | 5.4% | 5.6% | +0.2 |

The gap is flat, which is the falsifier. But the last four rows are
**identical**, and that is the tell: the longest log is 151 turns and the
median is 49, so truncating at 300, 600 or "all" truncates nothing.

The genuine range tested is **157 knots against 204**. At a 2% document
frequency ceiling that is a threshold of 3 versus 4 — the mechanism barely
differs between the two conditions being compared. A flat gap across that span
is what you would observe whether the account is right or wrong.

**So the prediction is untested, not refuted.** The corpus cannot test it: RP
logs are two orders of magnitude too short for a claim about how a
corpus-level statistic behaves as a corpus grows.

That distinction matters more than the outcome would have. A flat line from a
test that could not have shown a slope is not evidence of flatness — and
reporting it as a falsification would have been the same error as every
harness bug in this project, one level up: mistaking a property of the
instrument for a property of the thing.

**What would test it:** synthetic conversations at 100 / 400 / 1,600 / 6,400
turns with turn-shaped queries, spanning 50 to 3,000 knots. `long_bench.py`
already generates conversations at those lengths with real dialogue filler;
what it lacks is turn-shaped ground truth, which `rp_turnbench.py` derives from
a character's next reply. Joining the two is a day's work and would settle it.

Until then the honest status of dual representation is: **a mechanism with a
plausible account, one point of directional evidence across eleven logs, and a
prediction nobody has been able to test.**

---

## The query is the wrong instrument, and ranking is worse than not ranking

Six bridges were built to make the query reach further: PPMI co-occurrence,
static embeddings (model2vec), contextual embeddings (MiniLM), structural cord
expansion, write-time index terms, and a model-generated affordance web. All
six landed within noise of each other on the same population. The affordance
web — the one built on the *right* relation, "what does this word apply to"
rather than "what resembles it" — reached 18.5% of the unreachable facts and
converted 0.2% of them into the pack. The rescued knots ranked at a median of
35 where roughly 17 fit the budget.

Six failures with the same shape is not six bad ideas. It is one wrong
assumption underneath all of them.

### The control that settled it

Fill the pack by ignoring the query entirely.

| strategy | needed facts carried | knots per pack |
|---|---|---|
| lexical ranking | 9.31% | 11.6 |
| **random** | **14.51%** | 16.4 |
| shortest-first | 14.82% | 31.6 |
| longest-first | 13.73% | 10.6 |
| **greedy vocabulary coverage** | **16.60%** | 11.3 |

Random beats the retriever. That is not a statement about lexical scoring
being weak — a weak retriever should still beat chance. It means the ordering
is actively harmful: on a turn-shaped query only **23.2%** of the facts the
reply needs are lexically reachable at all, and the ranking spends the whole
budget inside that quarter, clustering on whatever shared a common word.
Random selection at least samples the other three quarters.

Coverage packing — greedily taking the knot that adds the most words the pack
does not yet contain — beats random too, at a third of random's knot count.
Spread, not relevance, is what a turn-shaped query can be served by.

### Why this cannot simply replace ranking

The same measurement on quiz-shaped queries, where a question names what it
wants:

| packing | A | B | C (stress) |
|---|---|---|---|
| lexical | 10/10 | 10/10 | 40/40 |
| pure coverage | 6/10 | 7/10 | **3/40** |

Coverage destroys quiz retrieval. 40/40 to 3/40 is not a regression, it is the
mechanism being wrong for that query shape. The two shapes want opposite
policies, so the question is not which to use but **how to tell them apart at
retrieval time**.

### COVERAGE_GATE

The top lexical score is itself the discriminator. A query that names its
target produces a high best-match score; a query that names the scene produces
a low one. So: trust the ranking when it clears a threshold, pack for coverage
when it does not.

Measured in the real harness (`eval_memory.py` at budget 300;
`rp_turnbench.py` across 11 logs at 1200):

| gate | A | B | C | turn-shaped |
|---|---|---|---|---|
| 0.0 (off, always lexical) | 10/10 | 10/10 | 40/40 | 4.3% |
| **1.0 (default)** | **10/10** | **10/10** | **40/40** | **5.0%** |
| 2.0 | 9/10 | 8/10 | 40/40 | 5.8% |
| 3.0 | — | 7/10 | 38/40 | 8.6% |
| 5.0 | — | 6/10 | 20/40 | 11.8% |
| 99.0 (always coverage) | — | 6/10 | 3/40 | 14.1% |

**Gate 1.0 is free.** Every chat suite is byte-identical to the baseline at
every budget (150/300/800), 98 unit tests pass, supersession passes. On turns
it carries 79 of 1289 needed facts against 72: **two logs improved (log5
3→6, log7 0→4), nine unchanged, none harmed.**

### A discrepancy worth recording

A standalone prototype of this gate, run before the mechanism was moved into
`pack()`, reported 5.59% → 7.53% for the same gates. The integrated version
reports 4.3% → 5.0%. The prototype filled from the raw ranked list; `pack()`
applies the relevance floor, the subject-focus damping and the reserve, so the
two are not measuring the same packer. **The shape of the curve reproduced;
the magnitude did not.** The integrated number is the one that counts, and it
is smaller — reported here rather than the flattering one, per the standing
rule in this file.

### What it costs and what it is worth

At 1.0 the gate fires rarely, which is exactly why it is free and also why the
gain is 0.7 points rather than 3. The value is not the 0.7. It is that the
dial exists and is monotone in both directions, so a deployment can choose:
assistant chat keeps 1.0, roleplay can take 3.0 and double its carried facts
for two questions on a stress suite that no roleplay ever asks.

### The conceptual result

Six bridges failed because they all tried to make the query reach further. The
query was never going to reach. **When retrieval cannot find the target, the
correct response is not to search harder — it is to stop searching and cover
the space instead.** The affordance web was not a bad idea; it was a good
answer to a question that had already been settled against.

---

# RETRACTION: every turn-shaped number in this file

Including the section immediately above, written the same day.

## The control

The turn benchmark infers ground truth from the conversation's own
continuation: a knot sharing a rare word with the next reply is counted as a
fact the reply needed. The replies in these logs were generated from the full
transcript, not from a pack, so nothing in that construction excludes
coincidence.

Test: score each turn's memory against the **wrong** reply. Same conversation,
same characters, same register — only the pairing broken.

| pairing | facts marked needed | share of real |
|---|---|---|
| the real reply | 1,289 | 100% |
| wrong reply, same conversation | 1,181 (5 seeds) | **92%** (89–95) |
| reply from a different conversation | 1,245 (5 seeds) | **97%** (95–97) |

A reply from a conversation with different characters, setting and plot marks
97% as many facts "needed" as the true continuation. Only 13% of the real
reply's needed set is also marked by the shuffled one — the metric is not even
stably wrong, it selects a fresh arbitrary subset each time.

The words it treated as distinctive evidence: `already`, `feel`, `back`,
`face`, `care`, `gentle`, `question`. `MAX_DOC_COUNT` is an absolute count
rather than a document frequency, and `content_words` does not stem, so common
words dodge it in a small store.

## Controlling the control

A control that says "shuffled equals real" regardless of target proves
nothing. Run identically against the chat suites, where the key is
hand-written — each question scored against a *different* question's keywords:

| | real | shuffled | separation |
|---|---|---|---|
| chat suites (hand-written key) | 100% | 2% | **42.9×** |
| turn benchmark (proxy key) | 100% | 92% | **1.09×** |

The instrument works. The benchmark does not.

Reproduce: `python audit_needed.py <logs>` and `python audit_control.py`.

## Withdrawn

Everything in this file derived from `rp_turnbench.py`:

- the 79.8% / 14.9% / 5.3% failure decomposition
- 23.2% lexical reachability; the 100% oracle ceiling in `turn_ceiling.py`
- 44.5% write-time bridging, and the index-terms result (+0.8 pts, "5 better,
  0 worse") — note this was already retracted once, then reinstated, and is
  now withdrawn a second time for a different reason
- bridge reach for PPMI (6.8%), static embeddings (7.5%), MiniLM (7.7%,
  47.6%@50), structural expansion, and the affordance web (18.5% / 0.2%)
- random 14.51% vs lexical 9.31% vs coverage 16.60% — the diversity control
- the `bridge_slots.py` +3.26 points
- the roleplay progression 4.1% → 6.3% → 9.6% → 11.2%
- the `PACK_RESERVE` sweep and its 0.5 roleplay default; `CORD_SHARE`;
  `SCENE_RESERVE`
- the COVERAGE_GATE turn column (4.3% → 5.0% → 8.6% → 14.1%)

The bridges are **uninterpretable, not refuted.** Each was scored on
retrieving a set we can no longer claim was needed.

## What survives

Anything with a hand-written answer key: the 119× cost result, the conditional
accuracy result and its boundary, the transcript failure modes, the extraction
finding, chat suites A/B/C, 98 unit tests, the supersession suite. These share
no instrumentation with the above.

`COVERAGE_GATE = 1.0` stays in the code because it was verified
byte-identical on the real suites at every budget — but its *motivation* is
withdrawn, and that is now said in the source.

## The lesson, stated as a rule

This is the fifth instrumentation failure in the project and the first to
reach publication. Four of the five are one error wearing different clothes: a
measurement was trusted because it produced plausible numbers, and no
condition was ever constructed under which it would have produced obviously
wrong ones.

> **Every instrument must be given a chance to fail visibly.** A knob that
> claims to disable a feature is verified by confirming the feature stopped
> happening. A generation cap is verified by measuring generation length
> against it. A proxy ground truth is verified by feeding it a wrong answer.
> Plausible output is not evidence that an instrument works.

The turn benchmark never produced an absurd number, never contradicted itself,
and each mechanism it evaluated failed in a way that appeared to explain the
last. That is what made the programme feel convergent rather than vacuous.
Cheap, plausible and self-confirming is the characteristic hazard of
evaluation without answer keys.

## Next

Build a turn-shaped benchmark with planted facts and a written key — the
`long_bench.py` discipline with turn-shaped queries instead of quiz questions
— and run `audit_control.py` against it **before** trusting a single number.

---

# A replacement benchmark, and what it says

`qontext-bench/turn_bench.py`. Built after the retraction, to the rule the
retraction produced: an instrument must be given a chance to fail visibly.

## Construction

Keep what made the retracted benchmark worth building, discard what killed it.

- **Kept**: queries are conversational turns, not quiz questions.
- **Discarded**: inferred ground truth. Every dependency is *written down* —
  the fact, the turn that needs it, and the words that prove it was carried.

Eighteen facts planted in an 800-turn conversation with DailyDialog filler and
10% decoys, the `long_bench.py` construction. Four are quiz-shaped, as a
sanity anchor. Fourteen are turn-shaped and labelled by the kind of gap the
retriever must cross: hypernym (shellfish → "seafood place"), reference
("your brother" → Joris), script (night shifts → "ring you at nine tomorrow
morning"), consequence (car in the garage until Friday → "collect me
Wednesday"), inference (vegan → "lasagne, loads of bechamel").

**The control runs first and gates the report.** Each turn is scored against a
different fact's key; if separation falls below 5×, the benchmark prints the
failure and exits non-zero without reporting accuracy.

## It discriminates

| seed | knots | separation |
|---|---|---|
| 7 | 170 | 6.9× |
| 11 | 166 | 6.9× |
| 23 | 154 | 9.6× |
| 42 | 177 | 9.6× |
| 99 | 175 | 12.0× |

Against 1.09× for the retracted benchmark. Quiz-shaped scores 4/4 on every
seed, so the memory is not broken upstream of the experiment.

## Result

**Turn-shaped: 2/14 (14%), identical on all five seeds.**

| gap kind | carried |
|---|---|
| quiz (anchor) | 4/4 |
| reference | 1/3 |
| script | 1/5 |
| hypernym | 0/1 |
| consequence | 0/3 |
| inference | 0/2 |

The two hits are the two where a content word survives the gap
("brother"/"brother", "repo"→project). Everything requiring an actual bridge
misses.

Nothing moves it:

| knob | turn-shaped |
|---|---|
| budget 300 → 800 | 2/14 → 2/14 |
| RELEVANCE_FLOOR 0.5 → 0.1 → 0.0 | 2/14 → 2/14 → 2/14 |
| COVERAGE_GATE 0.0 → 1.0 → 3.0 | 2/14 → 2/14 → 2/14 |

The pack averages 179 chars at a 300 budget and **192 at an 800 budget** — it
is not budget-bound, it is out of things it can find.

## COVERAGE_GATE is withdrawn as a default

Committed this morning at 1.0 on the retracted benchmark, where gate 3.0
appeared to double carried facts. On the sound benchmark it changes nothing at
any setting, so the default is now **0.0, off**.

The interesting part is gate 99 — pure coverage packing — which does not
merely score badly. **It fails the control at 0.7×.** A pack built without
reference to the query contains the right fact no more often than a wrong one,
so the metric can no longer separate them, and the benchmark refuses to report.

That is the mechanical explanation for the whole coverage episode. The
retracted metric was itself query-independent: it marked 97% as many facts
"needed" when handed a reply from an unrelated conversation. A
query-independent metric rewards a query-independent packer. Random beat
lexical, and coverage beat random, because neither was being asked to respond
to the query and neither was the metric. **The finding was circular, and the
new control detects the circularity directly.**

## What this does and does not establish

**Does:** the memory retrieves reliably when the query shares vocabulary with
the fact (4/4 quiz, 40/40 stress suite), and essentially not at all when it
does not (2/14). Neither ranking, budget, floor nor coverage changes the
second number. The bottleneck is reachability.

**Does not:** say that 14% is the achievable rate in real conversation. *We
wrote the gaps.* "Seafood → shellfish" and "bechamel → vegan" were chosen
precisely because lexical retrieval cannot cross them. The difficulty is
authored, not sampled.

So this is a **differential** instrument, not an absolute one. It is valid for
comparing mechanism A against mechanism B on a fixed set of gaps, which is
exactly what the six retracted bridges needed and never had. It is not valid
for claiming a real-world rate, and 18 facts is a small sample besides.

## What it is good for next

Re-run the six retracted bridges against it. Each was scored on retrieving a
set we could not claim was needed; here the need is written down, the gap
kinds are labelled, and the control gates the answer. A bridge that crosses
`hypernym` and `inference` gaps would now show up as exactly that, per
category, instead of as a percentage of an arbitrary population.

---

# The bridges, re-run — and a reversal

`qontext-bench/bridge_bench.py`. The six mechanisms were scored on a benchmark
that has since been retracted, so their results were uninterpretable rather
than refuted. This is the re-run, on `turn_bench.py`, with the shuffle control
gating every arm.

Method: the pack is filled lexically first, then the bridge adds proposals
while budget remains. Strictly additive — a bridge can add a fact the query
had no word for, but can never displace one the ranking wanted. A failure is
therefore attributable to reach.

## Result

3 conversation seeds, budget 800. `real`/`shuffled` are printed even for
failed arms, because "added bulk" and "found something and drowned it" are
different failures and the verdict alone cannot tell them apart.

| arm | real | shuffled | control | turn-shaped |
|---|---|---|---|---|
| baseline (lexical) | 6.0/18 | 0.67 | 6.9× | 6/42 |
| write-time index terms | 6.0/18 | 0.67 | 6.9× | 6/42 |
| index terms OFF | 6.0/18 | 0.75 | 8.0× | 6/42 |
| affordance web | 6.3/18 | 1.50 | 3.7× | **FAILED** |
| static embeddings, K=6 | 9.7/18 | 1.12 | 6.2× | **17/42** |

**Static embeddings work.** Version 1 dismissed them at "7.5% reach, within
noise of counted co-occurrence". On a benchmark with a written key they nearly
triple turn-shaped retrieval.

Five conversation seeds, K=6, per-seed rather than pooled:

| seed | lexical | +embed | control |
|---|---|---|---|
| 7 | 2/14 | 6/14 | 6.2× |
| 11 | 2/14 | 3/14 | 5.1× |
| 23 | 2/14 | 6/14 | 10.0× |
| 42 | 2/14 | 5/14 | 9.0× |
| 99 | 2/14 | 5/14 | 12.0× |

Every seed improves, none regresses. Wired into `pack()` and re-measured:
**23/70 (33%) against 10/70 (14%)**. Chat suites untouched at K=3 and K=6,
both budgets: 10/10, 10/10, 40/40. 98 tests pass, supersession PASS.

By gap kind, the profile is the interesting part:

| gap | lexical | +embed K=6 |
|---|---|---|
| hypernym (shellfish → "seafood") | 0/3 | **3/3** |
| reference ("your brother" → Joris) | 3/9 | 5/9 |
| script (night shifts → "ring you at nine") | 3/15 | 5/15 |
| consequence (garage till Friday → "collect me Wednesday") | 0/9 | 3/9 |
| inference (vegan → "lasagne, bechamel") | 0/6 | 1/6 |

Hypernym gaps close completely. Inference gaps stay shut, which is the
expected place for a bag-of-vectors model to fail.

## Why the reversal

The retracted benchmark's needed-set was 97% unchanged when handed a reply
from an unrelated conversation — it was very nearly a query-independent
metric. Embeddings retrieve *semantically related* knots. Retrieving
semantically related knots cannot help you retrieve an arbitrary set, so they
scored at chance.

**The mechanism was dismissed by a metric structurally incapable of rewarding
it.** This is the counterpart to the coverage result: the same broken metric
rewarded query-independent packing and punished query-dependent retrieval,
which is exactly the pair of errors it should be expected to make. Both are
now explained by one property of the instrument rather than by two separate
stories about retrieval.

## K is a real constraint, not a knob to maximise

| K | turn-shaped | control | shuffled | pack chars |
|---|---|---|---|---|
| 0 | 10/70 | 6.9× | 0.67 | 192 |
| 3 | ~15/70 | 9.1× | 0.75 | ~300 |
| 6 | 25/70 | 6.2× | 1.12 | ~350 |
| 10 | — | **4.9× FAILED** | 2.25 | 625 |

At K=10 the pack is swamped: both real and shuffled rise, separation drops
below the bar, and the benchmark refuses to report. The shuffled column is the
diagnostic — while it stays flat the bridge is adding signal; when it climbs
with the real score, it is adding bulk.

## What this costs and what it does not establish

**Costs a dependency.** `model2vec` (~30 MB, no torch). The library's claim to
be a single dependency-free file survives only because the bridge is optional
and off by default: `QontextMemory(bridge=fn, bridge_k=6)`.

**Does not establish a real-world rate.** We wrote the gaps. "Seafood →
shellfish" was chosen because lexical retrieval cannot cross it, so 33%
measures authored difficulty.

**The item sample is 14, not 70.** The five seeds vary the filler and decoys,
not the facts or the queries. Seed-to-seed agreement shows robustness to
distractors, not robustness to item choice. Fourteen turn-shaped items is a
small sample and the confidence interval on 33% is wide.

**The affordance web is not cleanly refuted.** It was generated over the
roleplay logs' vocabulary, not this benchmark's, so its failure here may be
coverage rather than mechanism. Rebuilding it against this vocabulary needs
the local model server and has not been done.

## Standing

This is the first positive retrieval result in the project measured on a
benchmark that passes its own control. It should be treated as one sound
result on a small authored item set — not as a solved problem, and explicitly
not as the 3× headline the raw numbers would support.

---

# The affordance web, with the coverage excuse removed

Regenerated over `turn_bench.py`'s own vocabulary — all 110 surface forms from
the 14 turn-shaped pairs, both the query side and the fact side. 108 linked.
Two generator bugs fixed first:

1. It prompted the model with **stems**, because the vocabulary came from
   `_words()`. The model was asked what `amaz` applies to.
2. Worse, it **keyed entries by the prompted string while `related()` looks
   them up by stem.** Any word whose stem differs from its surface form was
   stored under a key that could never be retrieved. The entry existed and was
   invisible.

## It still fails, and harder

| arm | real | shuffled | control |
|---|---|---|---|
| baseline | 6.0/18 | 0.67 | 6.9× |
| affordance web (RP vocab) | 6.3/18 | 1.50 | 3.7× |
| **affordance web (rebuilt)** | 8.0/18 | 2.62 | **2.6×** |
| static embeddings K=6 | 9.7/18 | 1.12 | 6.2× |

The rebuilt web raises real by 2.0 and shuffled by 1.95 — **they rise
together, nearly 1:1.** That is bulk, not signal. Embeddings raise real by 3.7
and shuffled by 0.45, an eight-fold difference.

## Not an artefact of the adapter

Before condemning it, the same reach test the retracted bridges were given,
with packing removed entirely — is the needed fact anywhere in the web's
proposals?

| expansions/word | min overlap | reached | candidate pool |
|---|---|---|---|
| 3 | 1 | 2/14 | 11 knots |
| 5 | 1 | 3/14 | 16 |
| 8 | 1 | 3/14 | 23 |
| any | **2** | **0/14** | **~0** |

Requiring two shared expansion words collapses the pool to nothing: **no knot
ever shares two expansion terms with a query.** Tightening the adapter does
not sharpen it, it kills reach. The ceiling is 3/14 at a cost of 23 extra
candidates, against 6/14 for embeddings at a cost of 6.

## Why — and it is not a quality problem

The expansions are *correct English*. That is what makes this interesting.

```
seafood  -> fish, shrimp, crab, lobster, oyster, squid, whale
bechamel -> sauce, milk, butter, flour, pasta, lasagna, soup
```

The needed knots say **shellfish** and **vegan**. Neither appears, and neither
should: `shellfish` is not a thing seafood *applies to*, it is a co-hyponym at
the same level of abstraction. `vegan` is not a thing bechamel applies to, it
is a property two inferential hops away.

The affordance relation expands **downward and outward into associated
concrete things**. The gaps need **sideways, at matched abstraction**.

## The conjecture is now contradicted, not merely unsupported

Version 1 argued that PPMI and embeddings all fail because they encode
*similarity*, whereas the required relation is *dependence on an unwritten
continuation* — a property no encoder computes. The affordance web was built
on that argument.

On a benchmark with a written key and a working control:

- the mechanism built on that argument reaches 3/14 and fails the control
- the mechanisms the argument dismissed reach 6/14 and pass it

Co-hyponyms appear in similar contexts, which is exactly what a contextual
embedding measures. **The needed relation was similarity after all.** The
argument was a plausible story that survived only because the metric behind it
could not reward the thing it was arguing against.

## Limits

One web, one model, one prompt template, 14 authored gaps. A different
elicitation ("name words that could substitute for X") might well behave like
an embedding — but at that point it is an embedding with extra steps and a
server dependency. Nothing here says structured lexical knowledge is useless
in general; it says *this* relation does not cross *these* gaps.

## Standing

This is the project's first **cleanly refuted** bridge. The six retracted ones
were uninterpretable — scored on a set we could not claim was needed. This one
had full vocabulary coverage, a written key, a control that passes on the
baseline, and a reach test independent of packing. It failed all three.

---

# A held-out suite, and the first result that survives one

Suite A has fourteen turn-shaped items, all written by us, and we had already
used it to tune K for the embedding bridge. Fitting a mechanism to fourteen
invented sentences is how a *sound* benchmark becomes an unsound result. The
chat suites have carried a tuned-on/held-out split since the beginning; the
turn benchmark had none.

**Suite B**: 24 facts, 20 turn-shaped, written afterwards and never tuned
against. Deliberately unlike A in surface features — workplace and civic
vocabulary instead of domestic, different names, different gap instances — so
it tests generalisation rather than resampling. Same specification.

The rule: a mechanism may be tuned on A. Results are reported on **both**,
always, and a gain that appears only on A is reported as overfitting rather
than as a finding.

## Two authoring defects B exposed immediately

1. *"My hearing aid battery died this morning"* never entered the store. The
   extractor rejected it, so the item was measuring extraction, not
   retrieval. `score()` now skips items whose fact is absent from memory and
   names them separately as an **extraction miss**. Two different failures,
   two different numbers.

2. *"What street does the user live on?"* was labelled `quiz` but shares no
   word with "my flat is on Weverstraat" — a hypernym gap wearing a quiz
   label, and it duly failed. A quiz anchor must repeat the vocabulary of its
   answer; that is the entire point of having one. Reworded to "Where is the
   user's flat?"

Both were our errors, and both were invisible until a second suite existed.

## Result

Budget 800, K=6, 2 conversation seeds per suite.

| arm | A (tuned-on) | B (HELD OUT) | verdict |
|---|---|---|---|
| baseline (lexical) | 4/28 (14%) 6.9× | 8/38 (21%) 12.8× | — |
| write-time index terms | 4/28 (14%) | 8/38 (21%) | no effect |
| index terms OFF | 4/28 (14%) | 8/38 (21%) | no effect |
| affordance web (rebuilt) | **FAILED 2.6×** | 8/38 (21%) 5.8× | no effect |
| **static embeddings** | **12/28 (43%)** | **16/38 (42%)** | **generalises** |

**+8 on A and +8 on B.** 43% against 42% — the held-out suite reproduces the
tuned-on gain almost exactly. This is the first result in the project to be
confirmed on items it was not fitted to.

Index terms are confirmed inert a third time, now on held-out data.

## Per gap kind, held-out suite B

| gap | lexical | +embeddings |
|---|---|---|
| hypernym | 0/8 | **4/8** |
| inference | 0/6 | **4/6** |
| reference | 6/8 | 6/8 |
| script | 0/8 | 0/8 |
| consequence | 2/8 | 2/8 |

The profile differs from A in one instructive way: on A, `inference` was the
category embeddings could not touch (1/6). On B they get 4/6. B's inference
items are *pregnant → Rioja*, *gluten → couscous*, *colourblind → amber/lime*
— all cases where the two terms genuinely co-occur in ordinary text. A's were
*vegan → bechamel* and *teetotal → wine tasting*, which need a hop through an
unstated middle term (dairy, alcohol).

So "inference" is not one category. It splits into gaps that distributional
co-occurrence already covers and gaps needing an intermediate concept, and our
label conflated them. **The category scheme is a hypothesis, not a
measurement**, and this is the first evidence it is partly wrong.

`script` is 0/8 on B and 1/5 on A — the hardest category for every mechanism
tried so far, and the one worth attacking next.

---

# The contextual encoder was supposed to be the cheap win. It wasn't.

Prediction on record before the run: model2vec is a *static* model — a bag of
well-trained word vectors with no context window, chosen deliberately as a
lower bound. A real contextual sentence encoder should beat it.

| arm | A (tuned-on) | B (HELD OUT) | control |
|---|---|---|---|
| baseline (lexical) | 4/28 (14%) | 8/38 (21%) | 6.9× / 12.8× |
| static (model2vec, 30 MB) | 12/28 (43%) | 16/38 (42%) | 6.2× / 19.2× |
| contextual (MiniLM, 88 MB) | 12/28 (43%) | **17/38 (45%)** | 5.7× / 10.7× |

**Identical on A, one item better on B.** One item in 38 is inside noise, and
MiniLM's control is *worse* on both suites (5.7× against 6.2×, 10.7× against
19.2×), meaning it also pulls in slightly more knots that match arbitrary
keys. The prediction failed.

Per gap kind on B the two are the same everywhere except hypernym, 4/8 → 5/8.

## What that tells us about the task

A contextual encoder's advantage over a bag of vectors is *composition* —
word order, negation, syntactic scope. These gaps do not need it. `seafood →
shellfish`, `Rioja → pregnant`, `couscous → gluten` are word-level
relatedness; the sentences around them are short and carry little structure
worth composing.

So the useful conclusion is practical and slightly deflationary: **use the
static model.** Three times smaller, no onnxruntime, no torch, and it does the
same job. The 88 MB buys one item.

## Note on getting MiniLM at all

`sentence-transformers` pulls a 526 MB torch wheel that stalled repeatedly
here, and `download.pytorch.org` is unreachable from this sandbox. Running the
same weights through their ONNX export (88 MB, `onnxruntime` + `tokenizers`)
gives the identical model with a different runtime — mean pooling over token
states then L2 normalise, which is what all-MiniLM-L6-v2's pooling layer does.
Sanity check: cos(seafood turn, shellfish knot) = 0.412 against
cos(seafood turn, Kubernetes knot) = 0.166.

Worth keeping as a pattern: a transformer encoder is available in a
dependency-light form when torch is not an option.

## Where the score now stands, held out

| gap | lexical | best bridge |
|---|---|---|
| hypernym | 0/8 | 5/8 |
| inference | 0/6 | 4/6 |
| reference | 6/8 | 6/8 |
| consequence | 2/8 | 2/8 |
| **script** | **0/8** | **0/8** |

`script` is untouched by every mechanism tried: lexical, index terms,
affordance, static and contextual embeddings all score 0/8 on B and ~1/5 on A.
These are the gaps where the fact constrains a *situation* rather than
resembling anything in the query — "I carry the pager every third weekend" vs
"fancy a long hike this Saturday". Nothing in the query is semantically near
the fact; what connects them is that one makes the other impossible.

`consequence` (2/8, unmoved) is the same shape. Together that is 16 of 38
held-out items on which no bridge has ever scored, and it is the next problem.

---

# RETRACTION: "script and consequence are unreachable by similarity"

Written one entry above, on the strength of `0/8` at K=6. It is wrong.

## The measurement I should have run first

Rank of the correct knot under embedding cosine, suite B, 172 knots:

| gap | ranks | median |
|---|---|---|
| script | 16, 23, 26, 106 | **24** |
| consequence | 8, 15, 27, 84 | **21** |
| reference | 1, 14, 35, 70 | 24 |
| hypernym | 5, 5, 100, 106 | 52 |
| inference | 1, 3, 117 | 3 |

The correct knot for a `script` item sits at median rank 24 of 172 — the 86th
percentile. Similarity reaches these perfectly well. **K=6 was cutting them
off.** "Nothing in the query is semantically near the fact; what connects them
is that one makes the other impossible" was a satisfying story fitted to a
score, not a property of the data.

## What is actually the constraint

Suite B, held out, static embeddings:

| budget | K | turn-shaped | control | pack chars |
|---|---|---|---|---|
| 800 | 6 | 16/38 (42%) | 19.2× | 408 |
| 800 | 30 | 17/38 | 7.4× | 793 |
| **1500** | **30** | **26/38 (68%)** | **5.4×** | 1448 |
| 3000 | 30 | 26/38 | **4.5× FAILED** | 1563 |

By gap kind, K=6@800 → K=30@1500:

| gap | K=6 | K=30 |
|---|---|---|
| script | 0/8 | **6/8** |
| consequence | 2/8 | **6/8** |
| hypernym | 4/8 | 4/8 |
| reference | 6/8 | 6/8 |
| inference | 4/6 | 4/6 |

The two categories declared unreachable are exactly the two that unlock. They
were never a semantic problem; they were the items ranked 15–30, which is
outside a six-slot bridge and inside a thirty-slot one.

## The real trade, stated honestly

42% → 68% held-out costs **3.6× the pack** (408 → 1448 characters) and drops
the control from 19.2× to 5.4×, which is barely over the 5.0 bar. At budget
3000 it fails outright at 4.5%. The mechanism is not "more is better" — a
larger pack is a less specific pack, and the control measures that directly.

So the residual is **budget-bound, not reach-bound.** That is a much more
tractable problem than the one I described, and it points somewhere different:
not at a new semantic mechanism, but at precision. Getting the rank-24 knot to
rank 5 would buy the same items at a quarter of the cost, and would not
degrade the control.

## Why this keeps happening

Sixth time. The pattern is identical each time: a number is produced, a
mechanism is invented to explain it, and the explanation is never given a
chance to be wrong. `0/8` supports "unreachable" and also supports "reachable
at rank 24 with a cutoff of 6", and I picked the one that sounded like a
finding.

The rank measurement cost one script and ninety seconds. It should have been
run before the sentence claiming a universal negative, not after.

> **A score of zero has at least two explanations: the thing is absent, or
> your cutoff is too small. Measure the rank before naming the cause.**

---

# Cheap hybrid rerank of the top-30: no effect, and a clean reason why

The stated problem: at K=30/budget 1500 the held-out score is 68%, but the
control drops to 5.4x. The correct knot for `script`/`consequence` sits at
median rank ~24 of 172 -- inside a 30-candidate pool, outside a 6-candidate
one. Precision, not reach, was the open question: could the top-30 be
reordered cheaply so a K=6 proposal list still catches rank-24 items?

Tried: cosine similarity plus `lambda * jaccard(query_words, knot_words)`,
reranking only within the top-30 (reach unchanged by construction), lambda
swept 0/0.25/0.5/1.0/2.0, tuned on A, confirmed on B.

**Instrument check first.** lambda=0 reproduces `bridge_bench`'s plain
cosine arm bit for bit -- A 17/42 (40%, 6.2x), B 23/57 (40%, 19.2x) -- so the
disable path is verified before trusting anything lambda != 0 shows.

**Result: flat at every lambda tried**, both suites, both real score and
control. Per-item rank shift explains why cleanly: the actual target items
(suite B) -- pager (rank 23), badge (16), kosher (106), passport (26), lift
(27), stitches (15), aws (8), banned (84) -- **never move, at any lambda**.
These gap kinds were written to share zero vocabulary with their query, so
their Jaccard term is 0 by construction, and so is almost every neighbor's
in their top-30 pool -- there is no lexical signal present to rerank with.
The only items that moved were a few already-easy ones, irrelevant to the
problem; higher lambda moved them more without touching the target
population.

Same "no effect" shape as write-time index terms, but this time the
per-item rank data explains the null instead of leaving it unexplained.
Rules out query-side term weighting too -- same signal, same wall.

---

# Adaptive K: a classifier that estimates whether lexical will fail

Reframing that unblocked the above: a gate doesn't need to identify the gap
*kind* correctly. It only needs to estimate whether lexical retrieval is
*likely to fail*, so the wide net (K=30/budget 1500) can be spent on the
predicted-hard minority instead of every query.

## v1 -- proposed rule-based baseline, unmodified

Five buckets (NONE/HYPERNYM/SCRIPT/CONSEQUENCE/INFERENCE) from surface cues:
pronouns, script verbs, consequence markers, hypernym nouns. Checked two
ways before trusting it:

**Exact gap-kind agreement: 7% (A), 16% (B).** Near-useless -- this
benchmark's "inference" items (`vegan -> bechamel`) carry no pronoun, verb,
or discourse marker at all, so a keyword rule cannot name them correctly.

**Binary (predicted-wide vs actually-needs-wide, rank > 6): the number that
matters.** A: TP=5 FP=3 TN=2 FN=4, recall 56%, precision 62%. B: TP=7 FP=2
TN=3 FN=7, recall 50%, precision 78%. Gap-kind naming is close to useless;
"will lexical fail" is not.

End to end (K=6/budget 800 when NONE, K=30/budget 1500 otherwise, 3 seeds):

| suite | score | control | avg pack |
|---|---|---|---|
| A (tuned-on) | 19/42 (45%) | **4.4x -- BELOW 5.0x BAR** | 900 chars |
| B (held-out) | 34/57 (60%) | 7.5x | 870 chars |

B: real gain over plain K=6 (60% vs 40%), control comfortably clears the
bar, and beats blanket K=30 (68%/**5.4x**) on control while using little
more than half the pack (870 vs 1448 chars). A fails the control gate.

## v2 -- three changes, each kept only after checking it against this data

- **`"that"` dropped from PRONOUNS.** It fired on 3 items across both
  suites (shellfish, utrecht, penicillin) and every one was a false
  positive -- `"that"` is doing determiner/expletive duty ("that new
  place", "is that a long trip") far more often than true anaphora here. No
  true positive depended on it; `"this"/"it"/"there"` each were
  load-bearing for a real catch and stayed.
- **`"come"` and `"found"` added to SCRIPT_VERBS.** Both are the literal
  missing word in a real miss (vegan: "...come over"; garage: "...come and
  collect..."; trello: "...bug I just found" -- `"found"` doesn't match the
  original rule's `startswith("find")` prefix check at all, an irregular-verb
  gap in the naive stemming). Checked for collisions before keeping: neither
  word appears in any suite's correctly-NONE items.
- **DIRECT branch added, WH-gated.** A WH-question that already names an
  entity/possessive ("Where does Alice work?") short-circuits to "trust
  lexical." Caught and fixed a bug before trusting it: the entity check
  originally treated capitalised `"I"` as a named entity, which wrongly
  demoted two already-correct catches (trello, heron) to misses. Fixed by
  excluding `{"i","i'll","i've","i'm","i'd"}` from the entity test.

Also tried and **rejected**: a `structural` DIRECT variant (proposed
separately) firing on ANY proper noun / quoted string / number-or-date /
possessive / two-or-more long words, no WH-gate required. Confusion matrix
(predicted category x actual need) shows why it fails: on suite B it
predicts DIRECT on 15 of 19 items -- it fires on almost any date or place
name, which ordinary conversational turns are full of regardless of
difficulty. Recall collapses to **7%** (from 50%), end-to-end score falls to
46% (barely above the 40% plain-K=6 floor). The WH-gate is not decoration;
removing it was tested, not assumed, and it lost.

## v2 result

Classifier: A recall 56%->**89%**, precision 62%->**80%** (TP=8 FP=2 TN=3
FN=1 -- the one remaining miss is "brief," below). B: recall unchanged at
**50%**, precision 78%->88% (TP=7 FP=1 TN=4 FN=7) -- the come/found
additions were mined from A and, honestly, generalised to zero of B's
misses. That's exactly the result the tune-on-A/report-on-B split exists to
catch, not a flaw in the split.

| suite | score | control | avg pack |
|---|---|---|---|
| A (tuned-on) | 22/42 (52%) | **4.4x -- still BELOW BAR** | 1006 chars |
| B (held-out) | 34/57 (60%) | 7.5x | 821 chars |

B is unchanged (the A-mined keyword additions didn't touch it either way).
A's score rose (45%->52%) and precision rose (fewer wrong triggers), but
**the worst-seed control number did not move at all** -- still exactly
4.4x. Per-seed: 4.4x / 4.9x / 5.9x (seed 23 also slid closer to the bar
this round, from 6.2x pre-v2).

**"brief" stays a documented miss, not a chased one.** Query: "Can you walk
me through how the caching layer works?" -- no pronoun, no script verb
match, no consequence marker, no hypernym noun; genuinely no surface cue a
keyword rule can use. Per the discipline of stopping once keyword coverage
hits its obvious cases rather than growing the list indefinitely to chase
one item, this is left as a known limitation of a rule-based gate rather
than patched with an ad hoc word.

**Verdict for shipping:** wh_gated adaptive K beats plain K=6 on real score
on both suites, and beats blanket K=30 on control on both suites, at
roughly half blanket K=30's pack cost. It should replace flat K=6 as the
default bridge configuration when the bridge is enabled. It does not
currently clear the control bar on suite A, on one of three conversation
seeds -- see below, left open rather than papered over.

---

# A/seed-7: the control failure survives a much better classifier

Improving the classifier (recall 56%->89%, FP 3->2 on A) should have
widened the control margin if classifier accuracy were still the limiting
factor. It didn't move -- 4.4x before and after. That ruled out "the
classifier isn't good enough yet" as the explanation and pointed downstream,
toward the benchmark's own construction. Three hypotheses tested in order,
two of them killed:

**1. Generic filler/background lexical density -- killed.** Mean pairwise
cosine among each suite's own facts, and mean fact-to-filler cosine: A
0.053 / 0.045, B 0.051 / 0.047. Statistically identical. "A's background
corpus has a heavier right tail" does not hold at the aggregate level.

**2. Refined to what the control actually measures.** The control checks
whether `pack(query_i)` contains an *other real fact's* keyword -- not
filler vocabulary. Measured, per query, how many of the suite's *other*
real facts land inside its own top-30 cosine neighbourhood (what a widened
bridge actually pulls in): **A averages 2.64 foreign facts per query, B
averages 2.10** -- despite B having 19 other facts available to collide
with against A's 13 (50% more pool, fewer actual collisions). Real,
reproducible, and the right-shaped object (query-driven, not corpus-mean).

**3. Fact-similarity graph -- did not reproduce (2), and is recorded as a
falsified explanation, not a discarded one.** Hypothesis: suite A's facts
form tighter clusters than B's, which would explain (2). Built the graph
(nodes = each suite's facts, edges = cosine above threshold), swept
threshold at four percentiles of the *pooled* distribution so neither suite
got a friendlier cutoff:

| threshold | A mean deg (norm.) | A clustering | A components | B mean deg (norm.) | B clustering | B components |
|---|---:|---:|---:|---:|---:|---:|
| 75th pctile | 4.11 (0.24) | 0.403 | 1 | 5.92 (0.26) | 0.340 | 1 |
| 85th pctile | 2.00 (0.12) | 0.135 | 3 | 3.92 (0.17) | 0.210 | 1 |
| 90th pctile | 1.44 (0.08) | 0.046 | 6 | 2.50 (0.11) | 0.160 | 3 |
| 95th pctile | 0.89 (0.05) | 0.130 | 11 | 1.17 (0.05) | 0.000 | 11 |

By normalised degree, **B is denser than A at every threshold**, and stays
one connected component longer while A fragments earlier. Only the
clustering coefficient favours A, and only at the loosest threshold -- it
flips by the 85th percentile. The fact-fact graph does not reproduce the
2.64-vs-2.10 asymmetry.

**Why, and what the right object actually is:** 2.64-vs-2.10 is a
query-to-fact relationship (each query, deliberately worded to share little
vocabulary with its *own* target, happens to land near *other* facts) --
not a fact-to-fact one. This benchmark's queries were hand-written
independently of fact-fact geometry, so a fact-only graph projects the
query distribution away and can erase the very asymmetry it's being asked
to explain. The right object is a query-to-fact bipartite graph, or
equivalently the per-query top-30 membership already measured in (2). Not
built -- (2) already answers "is there an effect" (yes) and a bipartite
graph would only sharpen "why," which is not blocking any current
engineering decision.

**Standing:** this is the evidence chain worth keeping, including the two
rejected hypotheses -- filler density, tested and killed; fact-graph
density, tested and killed; query-to-fact geometry, measured and real
(2.64 vs 2.10) but not yet explained at the mechanism level. The final
hypothesis wasn't the first plausible one; it's what remained after two
others were checked and failed. **A/seed-7's control failure (4.4x) is
recorded as an open, unexplained limitation of the shipped wh_gated
adaptive-K mechanism, not resolved by this session.**

> **Engineering shipped: wh_gated adaptive K, both suites' real score
> improved, B's control clears the bar. Research parked: why A/seed-7
> doesn't, pending a query-to-fact bipartite graph -- next cycle, not this
> one.**

---

# Wiring adaptive K into qontext_memory.py surfaced a real, pre-existing bug

`bridge_needs_wide()` (the wh_gated classifier, unchanged from above) and the
K/budget gate are now in `QontextMemory` itself: `bridge_classifier=...`,
`bridge_k_wide=30`, `bridge_budget_multiplier=1.875`, all opt-in --
`bridge_classifier=None` (the default) reproduces the pre-existing flat
`bridge_k` behaviour exactly.

Before trusting the wiring, diffed its output against the benchmark script's
independent reimplementation, item by item, same seed, same suite. First
run: **2 disagreements on suite B** (`lift`, `gluten` -- both consequence/
inference, both correctly needing the wide net). Root cause was not the
integration, it was a latent bug in `pack()` that predates today: when a
query matches zero knots lexically (`_ranked(query)` empty), the method
returned a single "newest knot" fallback immediately, before the bridge
section ever ran. That branch existed since `BRIDGE_K` was introduced and
had never been exercised end to end -- every prior bridge benchmark
(`bridge_bench.py`, `adaptive_k_bench.py`) reimplemented the fill loop
*outside* `QontextMemory`, calling `mem.pack()` only for the base lexical
slice, so this method's own internal bridge path had no test coverage
against real data until this diff. Fixed by letting all three early-exit
branches (empty-ranked fallback, `COVERAGE_GATE`, and the normal ranked
fill) build `packed` and fall through to one shared bridge section and one
return, instead of returning directly. Re-diffed: **0 disagreements**, both
suites. Full 3-seed reproduction through the real `QontextMemory.pack()`
call, not a bench reimplementation: **A 22/42 (52%), control 4.4x/4.9x/5.9x
per seed; B 34/57 (60%), control 7.5x/8.5x/8.5x** -- exact match to the
benchmarked numbers above. 98 unit tests, the supersession suite (27/27
safety, 0/1753 invariant collapses), and the chat suites (10/10, 10/10,
40/40) all still pass unchanged.

Kept as a finding in its own right: the bug was invisible for as long as it
was because the thing that would have caught it -- calling the real method
end to end and diffing against an independent implementation -- had never
been done. Two external reimplementations agreeing with each other proved
nothing about whether the actual shipped code path worked.

---

# The co-occurrence weave, re-tested against the current benchmark: still doesn't reach script/consequence

`qontext_weave.py`'s `WordWeave` -- PMI-normalised co-occurrence within a
12-token, sentence-bounded window, learned from raw text -- was measured
extensively before (see "The vocabulary weave: mechanism proven, data
starved" above), but only ever under `rp_turnbench.py`, the retracted
benchmark. It had never been run through `bridge_bench.py` against the
current suites with the current control. Two arms added, `weave (this
suite)` and `weave (WikiText, pretrained)`.

**`weave (this suite)`** -- trained fresh on `mem.entries()`, the same
fairness move used for `affordance web (rebuilt)`. Checked before running
the benchmark: `weave.stats()` on suite A's ~170 knots gives `{vocabulary:
426, pairs: 1168, woven: 13, tokens: 656}` -- only 13 words survive pruning
at all. Of the 14 turn-shaped queries, 13 get zero proposals; the one that
gets any (`drink`) proposes a knot ("Fenna's standup is on Thursday") that
has nothing to do with the query. Confirms the earlier "data starved"
finding at a much smaller scale: this corpus is nowhere near enough text
for the mechanism to have anything to say.

**`weave (WikiText, pretrained)`** -- the 10MB `weave.qw`, 40,327 words,
seeded from 43.9M tokens. Real vocabulary coverage this time, but a real
domain-mismatch risk stated before running: WikiText is encyclopedic prose,
not conversational text, so its co-occurrence patterns are topical
(`shellfish` -> `oysters, mussels, clams`; `wine` -> `grapes, cabernet,
sauvignon`), not situational. Some benchmark words aren't in it at all
(`vegan`, `bechamel`, `trello`, `pager`).

Budget 800, K=6, 3 seeds (the properly gated setting -- everything fails
control at K=30/1500, see below):

| arm | A | B | verdict |
|---|---|---|---|
| baseline (lexical) | 6/42 (14%) 6.9x | 12/57 (21%) 12.8x | -- |
| weave (this suite) | 7/42 (17%) 6.2x | 12/57 (21%) 6.4x | A only -- OVERFIT |
| weave (WikiText, pretrained) | 6/42 (14%) 5.3x | 15/57 (26%) 7.2x | B only |
| static emb (model2vec) | 17/42 (40%) 6.2x | 23/57 (40%) 19.2x | generalises |

Per gap kind, suite B:

| arm | hypernym | reference | script | consequence | inference |
|---|---|---|---|---|---|
| baseline | 0/12 | 9/12 | 0/12 | 3/12 | 0/9 |
| weave (this suite) | 0/12 | 9/12 | 0/12 | 3/12 | 0/9 |
| weave (WikiText, pretrained) | **3/12** | 9/12 | **0/12** | **3/12** | 0/9 |
| static emb | 5/12 | 9/12 | 0/12 | 3/12 | 6/9 |

**Direct answer to the question this was built to answer: no.** Neither
weave variant catches a single additional `script` item (0/12, both,
unchanged from baseline) or a single additional `consequence` item (3/12,
both, unchanged from baseline) -- the exact two categories the bridge exists
for. The pretrained weave's entire +3 gain on B is concentrated in
`hypernym`, a category static embeddings already cover better (5/12) and
contextual embeddings cover much better (8/12, see earlier section).

**Why, mechanistically, and not just "it didn't work":** `script` and
`consequence` gaps need a pragmatic/causal link -- an event implies a
constraint ("car's in the garage" implies "can't collect you Wednesday"),
not a topical one. Co-occurrence-in-text, whether counted fresh or over
43.9M WikiText tokens, encodes topical/encyclopedic relatedness --
`shellfish` sits near `oysters` because articles about shellfish discuss
oysters, the same relation `hypernym` gaps need (an instance sits near its
category). It has no mechanism for "these two situations imply each other"
because that relation isn't reliably signalled by two words sharing a
sentence, at any corpus size. This is the same wall the affordance web hit
from a different mechanism (typed, LLM-generated relations instead of
counted ones) -- two structurally different approaches to word-relatedness,
same two categories untouched by both.

## A correction, found while running this: the earlier 68%/5.4x number was 2-seed-optimistic

Re-running the full `bridge_bench.py` at K=30/budget=1500 with the project's
own default of 3 seeds (7, 23, 99) rather than the 2 used when this number
was first produced: `static emb (model2vec)` on suite B now shows
**FAILED(4.4x)**, not 68% at 5.4x. Verified in isolation, seed by seed,
with a script touching only `bridge_static`/`packer`/`score`/`control` --
no contact with the weave arms added today:

| seed | real | shuffled | ratio |
|---|---|---|---|
| 7 | 17 | 3.125 | 5.44x |
| 23 | 17 | 2.875 | 5.91x |
| 99 | 16 | 3.625 | **4.41x** |

Worst-of-2 (seeds 7, 23) reproduces the original 5.4x exactly. Worst-of-3
reproduces the FAILED 4.4x exactly. The original headline sampled only the
two seeds that happened to clear the bar. **The 68%-held-out/5.4x-control
figure for blanket K=30/budget=1500 is retracted as reported; the correct
figure, at the seed count this project treats as standard everywhere else,
is that this configuration fails its own control.** This does not change
any shipping decision -- adaptive K was already chosen over blanket K=30 for
independent reasons (RP_FINDINGS.md, "Adaptive K") -- but the number was
wrong on its own terms and stood uncorrected until this run.

> **Two-seed and three-seed numbers are not the same instrument. Report the
> seed count next to every ratio, and re-check old headline numbers when a
> later run happens to use a different one, rather than assuming they'd
> have agreed.**

## The LLM judge: the first mechanism to actually cross script/consequence

Six mechanisms had tried to bridge these two gap kinds and failed identically
-- PPMI co-occurrence, static and contextual embeddings, an affordance web
built by asking a model offline what a word applies to, cheap reranking of
the embedding shortlist. All six encode a form of similarity, and script/
consequence items are constructed to share no vocabulary with their query,
so there is nothing for a similarity measure to find.

`bridge_llm_judge`, added to `bridge_bench.py`, does something categorically
different: no table, built at query time. It lists every stored fact and the
query and asks the model directly which facts it would need to respond
sensibly -- explicitly including facts "only implied by what would happen
next," and deliberately un-filtered by any lexical or embedding shortlist
first, since that would silently discard exactly the items this arm exists
to catch. Chat endpoint, thinking disabled (`chat_template_kwargs:
{"enable_thinking": false}`), temperature 0, following the API convention
already established in `build_affordance_web.py`/`live_agent.py`.

Run by the user against their own llama-server (12B instruct, 8192 ctx,
`--reasoning-budget 0`) -- this arm cannot run from the sandbox:

| | A (tuned-on) | B (HELD OUT) |
|---|---|---|
| baseline (lexical) | 14% | 21%, 12.8x |
| llm judge | 83%, 10.7x | 72%, 12.4x |

Per gap kind, suite B: **hypernym 11/12** (was 0/12), **reference 12/12**
(was 9/12), **script 7/12** (was 0/12), **consequence 6/12** (was 3/12),
**inference 5/9** (was 0/9). Script and consequence had never moved off
0/12 and low-single-digits under any prior mechanism, including two runs of
this same benchmark. Both suites clear the 5.0x control bar with room
(10.7x/12.4x), and a repeat run reproduced the exact same real-hit counts
(35/42, 41/57) -- only the shuffled-side number moved slightly (11.6x ->
10.7x on A), consistent with the local server not being perfectly
deterministic even at temperature 0, not with the mechanism being flaky.

**Given a chance to fail visibly, and didn't.** `propose.errors` /
`.network_calls` were wired into the report specifically because a 72%/83%
number this good demanded checking whether it was silently propped up by
errors returning empty (indistinguishable in the score from "the model
looked and found nothing"). Second run: **1107 proposal calls, 123 actual
server round-trips (the rest served from the per-arm query cache), 0
errors.**

**The real cost, not hidden:** 123 real network round-trips to a 12B model
for one benchmark sweep (34 unique turn-shaped queries across both suites x
~3.6 candidate-pool variants per query, caching collapses everything else).
Every other arm in this file is microseconds; this one is seconds per
uncached query. That cost is why this cannot simply replace the embedding
bridge in production as-is -- the natural next step, not yet built, is
gating it behind the already-shipped `bridge_needs_wide()` classifier, so
the LLM is only asked on the minority of queries lexical retrieval is
already estimated to fail on, rather than on every turn.

## Gating the LLM judge behind bridge_needs_wide: wired into production

The gate proposed above is now built. Three pieces:

**`llm_judge_bridge.py` (new, `qontext-live/`).** The mechanism itself,
pulled out of `bridge_bench.py` into its own importable module --
`make_llm_judge(api_url, max_candidates, timeout)` returns the same
`propose(query, knots, topk)` shape every bridge in this project uses, plus
`probe(health_url)` for the up-front reachability check. `bridge_bench.py`
now imports from it instead of carrying its own copy, so there is one
implementation of the mechanism, not two that could quietly drift apart
after the next edit to either.

**`qontext_memory.py`: a new `wide_bridge` parameter on `QontextMemory`.**
`bridge_classifier` still decides narrow vs. wide, exactly as before. What
changes is what runs on a wide verdict: if `wide_bridge` is set, it runs
INSTEAD of the plain `bridge` for that call, at `bridge_k_wide`; if left at
its default (`None`), behaviour is byte-for-byte what adaptive-K already
shipped -- the same `bridge`, just widened k/budget. `bridge=None` with
`wide_bridge` set is also valid and deliberately supported: no cheap
mechanism at all, judge-only-on-hard-queries, useful for exactly the
live-agent case below where there's no embedding model in the loop.

Seven new tests in `test_qontext_memory.py` (`TestWideBridge`, 105 total
now, all passing) check this offline, with stub bridge functions instead of
a real model: narrow queries never reach `wide_bridge`; wide queries never
reach the cheap `bridge`; a `bridge=None`-plus-`wide_bridge` setup adds
nothing on queries the classifier doesn't flag and something on the ones it
does; a wide verdict's raised ceiling (`budget * bridge_budget_multiplier`,
the pre-existing adaptive-K contract) still holds even when `wide_bridge`
returns something far larger than any budget; an exception from
`wide_bridge` cannot break `pack()`; a classifier exception falls back to
the cheap `bridge`, never to `wide_bridge`. One test initially over-asserted
-- expected `wide_bridge` output to respect the *plain* budget, not
noticing the wide-verdict ceiling is already raised by design -- caught by
running it, not assumed correct because it looked right.

**`live_agent.py`: actually turned on.** `load_memory()` now probes the
server once at startup (`llm_judge_bridge.probe`) and, if it answers, sets
`bridge_classifier=bridge_needs_wide` and
`wide_bridge=llm_judge_bridge.make_llm_judge(...)` on the loaded memory.
If the probe fails, it prints a visible message and leaves both unset for
that session, rather than letting the first wide-flagged turn discover the
problem itself against a 90-second timeout. `bridge` stays `None` --
narrow queries (the majority) get plain lexical retrieval exactly as
before, at zero added cost or dependency; only the classifier-flagged
minority ever reach the model. `CONFIG["llm_judge"] = False` turns the
whole thing off. `--selftest` exercises `load_memory()` (no server present
in that environment) and confirms the probe-failure path prints and does
not raise.

Full suite (105 unit tests, `bridge_bench.py` end-to-end smoke test, and
`live_agent.py --selftest`) all pass after this wiring.
