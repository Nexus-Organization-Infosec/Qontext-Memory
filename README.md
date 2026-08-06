# Qontext

A quipu-inspired conversational memory for language models. Pure Python, one
file, no dependencies, Python 3.8+.

The Inca recorded information as knots on cords — not a transcript of
everything said, but a compact encoding of what was worth keeping. Qontext
stores a conversation the same way: short, self-contained facts ("knots"), and
`pack(query, budget)` returns only the ones a given question needs.

```
pip install qontext-memory
```

```python
from qontext_memory import QontextMemory

memory = QontextMemory()
memory.observe("user", "People call me Marta and I work as a nurse.")
memory.observe("user", "The report is due March 3rd, hard deadline.")

memory.pack("when is the report due?", 300)
# 'the report is due March 3rd'
```

## What it is for

A long conversation does not fit in a small context window, and sending all of
it is expensive even when it does. Qontext keeps a fixed-size summary that does
not grow with the conversation.

**Measured on 800-turn conversations whose filler is real human dialogue,
against a 12B model, three seeds:**

| | full transcript | Qontext pack |
|---|---|---|
| accuracy | 9.3 / 10 | 9.7 / 10 |
| prompt tokens per call | 13,853 | **116** |
| reasoning generated | 1,697 chars | 789 chars |
| prompt processing | 6.6 s | 0.4 s |

Three seeds cannot separate 9.3 from 9.7 — that is a tie. **The 119x reduction
is the result**: the same answers for one percent of the prompt.

**Re-verified under `speakers="all"`** (the current default; the table above
was originally measured under `speakers="user"`, the previous default). Same
command, same three seeds, same 800-turn conversation:

| | full transcript | Qontext pack |
|---|---|---|
| accuracy | 10.0 / 10 | 9.7 / 10 |
| prompt tokens per call | 13,853 | **117** |
| prompt processing | 5.8 s | 0.7 s |

The pack side — the one `speakers=` actually changes — held almost exactly:
same 9.7 accuracy, same single miss (still the assistant's name question,
still answered "Terry Graham"), 117 tokens against a previously-measured
116. The full-transcript side improved from 9.3 to 10.0, but that arm never
touches `QontextMemory` at all, so this isn't something the default change
gets credit for — most likely run-to-run model variance (sampling is not
deterministic at temperature 0.2), not re-investigated further. **The 119x
figure and the tied-accuracy claim both stand under the current default.**

On smaller models the picture is stronger and more conditional. A 9B scores
4.3/10 from a transcript full of plausible wrong answers against the pack's
8.0; a 4B scores 4/10 against 10/10. The honest statement is that *context
reduction improves accuracy when the context holds plausible wrong answers and
the model is at or past its ability to choose among them* — distractor density
sets the difficulty, model capability sets the threshold. On a capable model
you get the saving, not the accuracy.

`Qontext_Study_Report.pdf` is the full write-up, corrections included.

## Design rules

Three rules, each paid for with a failed run:

1. **A knot must name its subject.** "I work as a nurse" is unanswerable once
   cut from the conversation; "the user works as a nurse" survives alone.
2. **The payload is the point.** "People call me" without "Marta" is a knot
   tied around nothing. Never trim the name, day, place or number out of an
   entry.
3. **Fewer, better knots.** Density comes from selection, not truncation.
   Cutting entries short to meet a budget destroys the meaning the budget
   exists to protect.

## What is here

| | |
|---|---|
| `qontext_memory.py` | the library — extraction, ranking, supersession, eviction |
| `test_qontext_memory.py` | 91 tests, stdlib `unittest` |
| `qontext_cords.py` | records how knots hang together, walks the links at retrieval |
| `qontext_rp.py` | roleplay build: state-aware extraction, per-character subjects, scene packs |
| `qontext_weave.py`, `seed_weave.py` | optional word-association layer (off by default) |
| `live_agent.py` | a small chat agent using the memory |
| `eval_memory.py`, `eval_supersede.py`, `stress_conv.py` | offline evaluation suites |
| `bench/long_bench.py` | the long-conversation benchmark, with a real-dialogue filler mode |
| `bench/turing_bench.py` | an imitation-game evaluation that needs no answer key |
| `bench/build_filler.py` | rebuilds the DailyDialog filler (not redistributed — see Licence) |
| `extension/` | SillyTavern extension: a JS port with a parity test against the Python |
| `RP_FINDINGS.md`, `bench/LIVE_RUN_FINDINGS.md` | every measurement, including the ones that went against us |

```
python -m unittest -v test_qontext_memory
python live_agent.py
```

## Roleplay

`qontext_rp.RPMemory` is the same idea under roleplay's assumptions. The chat
extractor admits a sentence on a marker or a payload — a number, a date, a
capital — and roleplay's most important sentences have none of those. *"I am at
the harbour now, not the shrine"* was dropped entirely, leaving only *"Plans
changed"*. The RP build treats **state as payload**: place, possession,
condition, kinship and commitment, recognised structurally because the nouns of
a setting cannot be known in advance.

Measured on 11 public roleplay logs, turn-shaped queries at budget 1200: 4.1%
of needed facts carried by the chat build, **9.9%** by the RP build. That is a
doubling, and it is still one fact in ten — the turn-shaped task is mostly
unsolved, and now measured rather than assumed.

## SillyTavern extension

Copy `extension/qontext/` into
`SillyTavern/public/scripts/extensions/third-party/`. Three modes: regular
(off), augment, replace.

The vocabulary tables are **generated** from the Python source rather than
retyped, and `extension/parity.mjs` replays scenarios through both
implementations and fails on any divergence. It found a real bug during the
port.

## The word weave, and why it is off

`qontext_weave.py` links words by co-occurrence so a query can reach a knot it
shares no word with. The mechanism works — seeded from WikiText-103 it knows
`vampire → slayer, dracula` and `allergic → anaphylaxis, asthma`. Measured on
real logs it was neutral to slightly harmful at every setting. Kept, wired,
documented, and `None` by default.

## Honest limitations

- The ten benchmark questions are ours, even where the conversation is not.
- Measurements come from one machine and two local quantised models.
- Answers are scored by keyword match, not judgement.
- The roleplay result has no model-in-the-loop validation, only a proxy.

## Licence

**PolyForm Noncommercial License 1.0.0** — see `LICENSE`.

Free to use, modify, and share for research, teaching, personal projects, and
by non-profit, educational and government organisations. **Commercial use is
not permitted.** For a commercial licence, open an issue.

This is *source-available* rather than open source in the OSI sense: the
"no commercial use" restriction is what the OSI definition excludes. Calling it
source-available is the accurate description.

The DailyDialog filler used by `bench/long_bench.py --filler daily` is **not
redistributed here** — DailyDialog carries its own licence, and
`bench/build_filler.py` rebuilds the file from the original dataset in one
command.
