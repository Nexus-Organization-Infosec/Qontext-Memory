#!/usr/bin/env python3
"""Does the index-term benefit grow with memory size, as predicted?

Written before measuring. The dual-representation account says the gain comes
from a document-frequency ceiling keeping background words out of the index.
A ceiling can only bind once there are enough knots for words to *become*
background: in a small memory nothing is common, the ceiling never fires, and
index terms should behave exactly like having none.

    prediction: the gap between INDEX_TERMS on and off widens with knot count.
    falsifier:  a flat gap. Then it is a constant that happened to help.

Measured by truncating each log to a prefix, so the same conversations are
replayed at several memory sizes.
"""
import io
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path("/sessions/admiring-sharp-keller/mnt/RP_Logs")
EXCLUDED = {"log8.txt", "log12.txt"}
PREFIXES = (60, 150, 300, 600, 10 ** 6)     # turns of each log to replay
BUDGET = 1200


def score(module, prefix):
    """Needed facts carried, replaying only the first `prefix` turns."""
    from rp_probe import load
    from rp_turnbench import content_words, needed_knots, RECENCY_WINDOW
    needed_total = carried = knots_seen = logs = 0
    for path in sorted(ROOT.glob("*.txt")):
        if path.name in EXCLUDED:
            continue
        turns = load(path)[:prefix]
        if len(turns) < 20:
            continue
        logs += 1
        mem = module.QontextMemory(max_entries=10 ** 6, speakers="all")
        for i, (speaker, is_user, text) in enumerate(turns):
            reply = turns[i + 1][2] if i + 1 < len(turns) else None
            if is_user and reply and len(mem):
                free = set()
                for _, _, earlier in turns[max(0, i - RECENCY_WINDOW):i + 1]:
                    free |= content_words(earlier)
                needed = needed_knots(mem, reply, free)
                if needed:
                    pack = set(mem.pack(text, BUDGET).split("\n"))
                    needed_total += len(needed)
                    carried += len(needed & pack)
            mem.observe("user" if is_user else speaker, text)
        knots_seen += len(mem)
    return (100.0 * carried / max(1, needed_total),
            knots_seen / max(1, logs), needed_total)


def build(terms):
    src = Path("qontext_memory.py").read_text(encoding="utf-8")
    src = src.replace("INDEX_TERMS = 10 ", "INDEX_TERMS = %d " % terms)
    ns = {"__name__": "qontext_memory"}
    exec(compile(src, "qontext_memory.py", "exec"), ns)
    module = type(sys)("qontext_memory")
    for key, value in ns.items():
        setattr(module, key, value)
    sys.modules["qontext_memory"] = module
    for name in ("rp_probe", "rp_turnbench"):
        sys.modules.pop(name, None)
    return module


def main():
    print("prediction: the on-off gap widens with memory size.")
    print("falsifier:  a flat gap.\n")
    print("  %-8s %-9s %-8s %-8s %-8s %s"
          % ("turns", "knots/log", "needed", "off", "on", "gap"))
    for prefix in PREFIXES:
        off_module = build(0)
        with contextlib.redirect_stdout(io.StringIO()):
            off, knots, needed = score(off_module, prefix)
        on_module = build(10)
        with contextlib.redirect_stdout(io.StringIO()):
            on, _k, _n = score(on_module, prefix)
        label = "all" if prefix > 10 ** 5 else str(prefix)
        print("  %-8s %-9.0f %-8d %-8.1f %-8.1f %+.1f"
              % (label, knots, needed, off, on, on - off))
    return 0


if __name__ == "__main__":
    sys.exit(main())
