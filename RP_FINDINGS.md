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
