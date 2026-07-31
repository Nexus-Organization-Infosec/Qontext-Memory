#!/usr/bin/env python3
"""Emit qontext/tables.js from the Python vocabulary tables.

Retyping 437 markers and 216 stop words by hand guarantees drift, and every
drift shows up later as an unexplained gap between the Python measurements and
what the extension actually does. Run this after changing any table.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import qontext_memory as qm  # noqa: E402

OUT = Path(__file__).with_name("qontext") / "tables.js"


def js_set(name, values):
    return "export const %s = new Set(%s);\n" % (
        name, json.dumps(sorted(values), ensure_ascii=False))


parts = ["""// GENERATED FROM qontext_memory.py — do not hand-edit.
// Regenerate with:  python qontext-st/build_tables.py
"""]
for name, table in (("STOP", qm.STOP), ("MARKERS", qm.MARKERS),
                    ("CORE_MARKERS", qm.CORE_MARKERS),
                    ("FRAME_DROP", qm.FRAME_DROP), ("FLAGGED", qm.FLAGGED),
                    ("DAYS", qm.DAYS), ("MONTHS", qm.MONTHS),
                    ("NUMBER_WORDS", qm.NUMBER_WORDS), ("AGREE", qm._AGREE)):
    parts.append(js_set(name, table))
parts.append("export const SYNONYMS = %s;\n" % json.dumps(
    {k: sorted(v) for k, v in qm.SYNONYMS.items()}, ensure_ascii=False))
parts.append("export const TOPIC_VOCAB = %s;\n" % json.dumps(
    qm.TOPIC_VOCAB, ensure_ascii=False))
parts.append("export const IMPORTANCE_TIERS = %s;\n" % json.dumps(
    [[sorted(w), s] for w, s in qm.IMPORTANCE_TIERS], ensure_ascii=False))
pattern = qm.OPENERS.pattern
assert "(?P<" not in pattern, "opener pattern needs porting by hand"
parts.append("export const OPENERS_SOURCE = %s;\n" % json.dumps(pattern))
parts.append("export const OPENERS_FLAGS = %s;\n" % json.dumps(
    "gi" if qm.OPENERS.flags & re.I else "g"))

OUT.write_text("\n".join(parts), encoding="utf-8")
print("wrote %s (%.1f KB)" % (OUT, OUT.stat().st_size / 1024))
