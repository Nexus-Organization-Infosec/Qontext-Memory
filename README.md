# Qontext Memory

A conversational memory for language models, shaped like a quipu: facts are
**knots** — short, self-contained, third-person statements — and each prompt
carries only the knots relevant to it, instead of the whole transcript.

The point is not saving tokens. It is that **small models answer better from a
dense pack than from a full transcript**: 10/10 correct from a 300-character
pack against 4/10 from the transcript it was distilled from, in the benchmark
this came out of. Attention is scarcer than context.

- Single file, standard library only, no dependencies.
- Python 3.8+. Thread-safe. No side effects on import.
- `qontext_memory.py` is the whole library — copy it into your project.

## Install

```
cp qontext_memory.py your_project/
```

That's it. No packaging, no dependencies — one file and the standard library.

## Use

```python
from qontext_memory import QontextMemory

mem = QontextMemory.load("qontext.qx")          # empty if the file is missing

mem.observe("user", "Morning! People call me Marta and I work as a nurse.")
mem.observe("assistant", "Nice to meet you, Marta.")
mem.observe("user", "Oh, before I forget: the demo is on Friday at 10:00.")
mem.observe("user", "The traffic was awful today, took me an hour to get home.")

print(mem.entries())
# ['People call the user Marta and the user works as a nurse',
#  'the demo is on Friday at 10:00']

print(mem.pack("When is the demo?", budget=300))
# 'the demo is on Friday at 10:00'

mem.save("qontext.qx")
```

Two things to notice: the traffic complaint was not stored, and "I work as a
nurse" became "the user works as a nurse" — a knot cut from the cord has to
still name its subject.

### In a chat loop

```python
def build_prompt(mem, history, user_message):
    facts = mem.pack(user_message, budget=300)
    system = "You are a helpful assistant."
    if facts:
        system += "\n\nKnown facts:\n" + facts
    return ([{"role": "system", "content": system}]
            + history[-6:]                       # short recency window
            + [{"role": "user", "content": user_message}])
```

Then after each turn:

```python
mem.observe("user", user_message)
mem.observe("assistant", reply)
mem.save("qontext.qx")
```

`live_agent.py` is a complete working example: a chat agent against a local
llama.cpp server that observes every turn, packs every prompt, and persists to
`qontext.qx`. Run `python live_agent.py --selftest` to exercise it without a
server.

## API

| Method | What it does |
|---|---|
| `observe(speaker, text)` | Watch a message. Only `"user"` text creates knots. Returns the new ones. |
| `add(knot)` | Store a fact directly, skipping extraction. Returns `True` if new. |
| `pack(query, budget=300)` | The densest relevant knots that fit in `budget` characters. |
| `entries()` | Every knot, oldest first. |
| `forget(pattern)` | Drop knots containing `pattern`. Returns how many. |
| `clear()` | Drop everything. |
| `explain(query, budget)` | `[(score, in_pack, text)]` — why `pack()` chose what it chose. |
| `stats()` | `observed_chars`, `stored_chars`, `entries`, `density`, `max_entries`. |
| `save(path)` / `load(path)` | Atomic write; `load` returns an empty memory rather than raising. |
| `serialize()` / `deserialize(data)` | The same thing as bytes, if you store it yourself. |

`len(mem)`, `iter(mem)` and `knot in mem` work as you would expect.

## Behaviour worth knowing

**It forgets on purpose.** `max_entries` (default 500) is a hard ceiling. When
it is reached, the least valuable knots go first: never retrieved, then least
distinctive, then oldest. Distinctiveness matters — evicting purely by age
loses the user's name on turn 3000 of a chatty session while keeping three
thousand variations of "the meeting ran long".

**Corrections replace, they do not accumulate.** "My manager is Priya" followed
by "My manager is Tomas now" leaves one knot, the current one — and the
replacement inherits the standing of what it replaced, so correcting a fact you
ask about constantly does not make it evictable. The correction does not have
to be worded like the original.

This works on the knot's **frame**: what is left after removing its payload
(capitalised names, numbers, weekdays, months, hyphenated codenames) and its
filler (correction markers, vague time nouns, generic change verbs). Two knots
replace each other only if their frames are identical.

**Distinct facts that share vocabulary never merge.** That is the property the
frame exists to protect, and it is enforced conservatively: anything the frame
does not recognise stays in it as itself, so `dog`/`cat`, `daughter`/`brother`,
`manager`/`supervisor`, `report`/`invoice` and `GPU`/`CPU` produce different
frames. Numbers hanging off a noun are identifiers rather than payload, so
"the manager of team 5" and "team 7" stay separate despite sharing four words
in five. Only three merges are hand-allowed — name/called, live/based/situated,
due/deadline — because saying those the other way means the same thing.

The suite proves this two ways: 25 hand-picked shared-vocabulary pairs that
must both survive, and every pair of facts within a conversation
cross-multiplied (1753 pairs), where zero merges are allowed. `eval_supersede.py`
runs both.

*Known limitation:* a correction with no relation word at all — "Actually I'm
in Antwerp now" after "I live in Rotterdam" — has an empty frame and will not
supersede, because the only thing that could link them is the preposition, and
"I'm in Antwerp" is not distinguishable from "I'm in IT" without guessing.
Guessing there would risk merging real facts, so it does not.

**Nothing it is handed can crash it.** `None`, bytes, integers, control
characters, 200 KB of regex metacharacters — all fine. A corrupt memory file
costs the user their history, not their session.

**The pack never exceeds its budget.** If nothing matches the query it sends
the single newest knot rather than nothing, so the model still has grounding.

## Tuning

Constants at the top of the file:

| Constant | Default | Effect |
|---|---|---|
| `DEFAULT_BUDGET` | 300 | characters of pack per prompt |
| `DEFAULT_MAX_ENTRIES` | 500 | hard ceiling on stored knots |
| `MAX_ENTRY_CHARS` | 120 | a knot longer than this is not a knot |
| `SUPERSEDE_SIMILARITY` | 0.6 | word overlap at which a knot replaces another |
| `RELEVANCE_FLOOR` | 0.5 | drop knots scoring under this fraction of the best |
| `LENGTH_NORM` | 0.5 | how strongly to prefer shorter knots (0 = off) |

The vocabulary tables — `MARKERS`, `OPENERS`, `STOP`, `SYNONYMS` — decide what
counts as a fact and how questions reach statements. They are the real tuning
surface. Every table is normalised through `_stem()` at import, and so is the
text they are matched against; if you add entries by hand, add plain words and
let the normaliser handle them.

## Testing

```
python test_qontext_memory.py        # 80 tests: contract, fuzz, persistence, threads
python eval_memory.py                # recall / density / noise on three conversations
python eval_memory.py --perf         # pack() latency as the memory grows
python eval_supersede.py             # corrections caught / distinct facts protected
python qontext_memory.py demo        # a small demo
```

`eval_memory.py` scores the three conversations in `conversations.py`: A (the
one the extractor was tuned on), B (held out, different phrasings), and C (150
turns, 40 facts, deliberate distractors). Current numbers:

| suite | recall @150 | recall @300 | density |
|---|---|---|---|
| A | 10/10 | 10/10 | 0.30 |
| B | 10/10 | 10/10 | 0.41 |
| C | 37/40 | 40/40 | 0.48 |

`pack()` stays under a millisecond against 10,000 knots.

There is also a CLI for looking at a memory file directly:

```
python qontext_memory.py show   qontext.qx
python qontext_memory.py stats  qontext.qx
python qontext_memory.py pack   qontext.qx "when is the demo?"
python qontext_memory.py why    qontext.qx "when is the demo?"
python qontext_memory.py forget qontext.qx "bikkel"
```

## Design rules

Three, each paid for with a failed run:

1. **A knot must name its subject.** Entries saying "I" become unanswerable
   once cut from the cord; "the user works as a nurse" survives alone. This
   single fix took the benchmark from 6/10 to 10/10.
2. **The payload is the point.** "People call me" without "Marta" is a knot
   tied around nothing. Never trim the name, day, place or number out.
3. **Fewer, better knots.** Density comes from selection, not truncation.
   Cutting entries short to meet a budget destroys exactly the meaning the
   budget exists to protect.

## Where this came from

Qontext started as a study of small local models — "can a 4B model build its own
context memory with tools, and is the structure any good?" The benchmark answer
was that a 26-turn transcript gave the model 4/10 correct answers, while a
300-character pack distilled from that same transcript gave 10/10. Reducing
context was not a cost saving; it was an accuracy win, because a small model's
attention runs out long before its context window does.

The three design rules above are the residue of four failed runs. Everything
else here — the eviction policy, the supersession frames, the relevance floor —
exists because a measurement said it helped, and several plausible ideas were
reverted because the measurement said they did not.

## License

MIT. See `LICENSE`.
