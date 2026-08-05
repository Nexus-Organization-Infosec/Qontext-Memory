#!/usr/bin/env python3
"""Emit parity cases for the assistant-chat port from the Python
implementation, for parity_chat.mjs."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qontext_memory import QontextMemory  # noqa: E402

SCENARIOS = [
    ("sample", [
        ("user", "Morning! Quick intro since we haven't talked before: people call me Marta."),
        ("assistant", "Nice to meet you, Marta. What shall we look at today?"),
        ("user", "I'm based in Utrecht, near the old canal."),
        ("assistant", "Utrecht is lovely. Go on."),
        ("user", "I work as a nurse, mostly night shifts, so I reply at odd hours."),
        ("assistant", "Good to know, no rush on replies then."),
        ("user", "The traffic was horrible this morning, took me an hour to get home."),
        ("assistant", "An hour! That sounds exhausting after a night shift."),
        ("user", "Lately I code in Rust for fun, little command line things."),
        ("assistant", "Rust is a great choice for CLI tools."),
        ("user", "Oh before I forget: the sprint demo is on Friday at 10:00."),
        ("assistant", "Noted, sprint demo Friday at 10:00."),
        ("user", "Our project is codenamed heron-nest, silly name but it stuck."),
        ("assistant", "heron-nest, got it. Who picked that?"),
        ("user", "Long story. Anyway, my dog is called Bikkel and he ate my charger cable."),
        ("assistant", "Bikkel sounds like a handful!"),
        ("user", "He is. By the way, please keep explanations brief, I skim a lot."),
        ("assistant", "Will do, brief it is."),
        ("user", "Also important: the report is due March 3rd, hard deadline."),
        ("assistant", "March 3rd, noted as a hard deadline."),
        ("user", "We track tasks in Trello, in case you need to reference the board."),
        ("assistant", "Understood, Trello board it is."),
    ], [
        ("When is the report due?", 300),
        ("what is the dog called?", 300),
        ("tell me about work", 300),
        ("where does the user live?", 150),
        ("what does the user prefer?", 200),
    ]),
    ("questions-and-address", [
        ("user", "Do you think it will rain tomorrow?"),
        ("user", "You should really try the new bakery on Kerkstraat."),
        ("user", "I told you about my sister Vesna already, she lives in Ghent."),
        ("user", "My allergy is to peanuts, please remember that."),
        ("assistant", "Understood, no peanuts."),
        ("user", "We use Postgres for the database and Redis for the cache."),
    ], [
        ("is the user allergic to anything?", 300),
        ("what does the user use for storage?", 300),
    ]),
    ("padding-and-eviction", [
        ("user", "People call me Chance and I'm based in Antwerp."),
        ("user", "My manager is Priya."),
        ("user", "My manager is Tomas now, Priya moved teams."),
        ("user", "The weather was strange again this morning, number 1."),
        ("user", "The tram kept me up this morning, number 2."),
        ("user", "My coffee went cold as usual, number 3."),
        ("user", "The neighbours made no sense of all things, number 4."),
    ], [
        ("who is the user's manager?", 300),
        ("where is the user based?", 150),
    ]),
]

out = []
for name, turns, queries in SCENARIOS:
    memory = QontextMemory()
    extracted = [memory.observe(who, text) for who, text in turns]
    out.append({
        "name": name, "maxEntries": memory.max_entries,
        "turns": [list(t) for t in turns], "extracted": extracted,
        "packs": [[q, b, memory.pack(q, b)] for q, b in queries],
        "entries": memory.entries(),
        "stats": memory.stats(),
    })
Path(__file__).with_name("chat_cases.json").write_text(
    json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote chat_cases.json: %d scenarios" % len(out))
