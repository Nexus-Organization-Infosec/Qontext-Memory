#!/usr/bin/env python3
"""Emit parity cases from the Python implementation for parity.mjs."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from qontext_rp import RPMemory  # noqa: E402

SCENARIOS = [
    ("harbour", [
        ("Carmine", 'I hate the harbour. "Too many people, and they all stare," she mutters.'),
        ("User", "*I take her hand* Then we go inland. My name is Chance, by the way."),
        ("Carmine", 'She laughs. "Chance. Of course it is." Her fangs catch the lamplight.'),
        ("Carmine", "I was turned in 1878, outside Marseille. I don't talk about it."),
        ("User", "We should meet at the shrine by the new moon."),
        ("Carmine", 'Carmine nods slowly. "The shrine, then. I will be there before you."'),
        ("Carmine", "I am wearing the grey coat tonight."),
        ("Carmine", "I am at the harbour now, not the shrine. Plans changed."),
        ("Carmine", "My sister Vesna is still alive. I promised her I would return."),
    ], [("*I lean over and kiss her cheek*", 600),
        ("where are you now?", 300),
        ("who is your sister?", 200)]),
    ("plain", [
        ("User", "People call me Marta and I work as a nurse."),
        ("User", "The sprint demo is on Friday at 10:00."),
        ("User", "My dog is called Bikkel."),
        ("Anna", "I live in Utrecht, near the old canal."),
    ], [("what is the dog called?", 300), ("tell me about work", 300)]),
    ("state-churn", [
        ("Vesna", "I am in the forest. It is cold."),
        ("Vesna", "I am at the tavern now."),
        ("Vesna", "I am wounded and afraid."),
        ("Vesna", "I promised the captain I would not run."),
    ], [("*I follow her*", 400), ("is she hurt?", 200)]),
]

out = []
for name, turns, scenes in SCENARIOS:
    memory = RPMemory()
    extracted = [memory.observe(who, text) for who, text in turns]
    out.append({
        "name": name, "reserve": memory.reserve, "maxEntries": memory.max_entries,
        "turns": [list(t) for t in turns], "extracted": extracted,
        "scenes": [[q, b, memory.scene(q, b)] for q, b in scenes],
    })
Path(__file__).with_name("cases.json").write_text(
    json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print("wrote cases.json: %d scenarios" % len(out))
