#!/usr/bin/env python3
"""
Live chat agent with a Qontext memory layer.

Instead of resending the whole conversation every turn, the agent sends:
  1. a Qontext pack  - dense entries relevant to your latest message
  2. a recency window - only the last few turns, verbatim

Everything older lives only in the memory (the knots). The full history
is tracked but never sent, so each turn prints what it would have cost.
Memory persists to qontext.qx - quit, restart, and it still knows you.

Usage:
    llama-server -m your-model.gguf --port 8080 -c 8192   (other terminal)
    python live_agent.py

Commands inside the chat:
    /mem            show all stored entries (the knots)
    /stats          memory + savings statistics
    /why QUESTION   show how the knots were ranked for that question
    /forget TEXT    drop knots matching TEXT (no TEXT = wipe everything)
    /quit           exit (memory is saved automatically every turn)

Self-test without a server:  python live_agent.py --selftest
"""

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import llm_judge_bridge
from qontext_memory import QuipuMemory, bridge_needs_wide

CONFIG = {
    "api_url": "http://127.0.0.1:8080/v1/chat/completions",
    "pack_budget": 300,        # chars of Qontext facts per prompt
    "recency_window": 6,       # last N messages sent verbatim
    "max_tokens": 512,
    "temperature": 1.0,        # Gemma 4 generation settings
    "top_k": 64,
    "top_p": 0.95,
    "memory_file": "qontext.qx",
    "max_knots": 500,          # hard ceiling; least useful are evicted first
    # Ask the model itself, at query time, which stored facts it needs --
    # but only on turns bridge_needs_wide() predicts plain retrieval will
    # miss. See llm_judge_bridge.py: first mechanism in this project to
    # cross the script/consequence gap kinds, at a real cost of seconds per
    # query rather than microseconds. That cost is why it is gated, not run
    # on every turn -- most turns never reach it. Off costs nothing extra;
    # on costs one extra call to the same server `chat()` already uses, on
    # the minority of turns flagged wide.
    "llm_judge": True,
    "bridge_k_wide": 10,        # facts the judge may add on a flagged turn
}

HERE = Path(__file__).parent
MEM_PATH = HERE / CONFIG["memory_file"]

SYSTEM = ("You are a helpful, concise assistant. "
          "Under 'Known facts' are things learned earlier in this or past "
          "conversations. Treat them as true and use them when relevant.")


def load_memory():
    """Never fails: a missing or corrupt memory file yields an empty memory.

    LLM-judge wiring happens here, not in __init__/load(), because it needs
    a live probe of the server -- bridge/bridge_classifier/wide_bridge are
    plain instance attributes, so setting them after construction is exactly
    as valid as passing them to QontextMemory(...), and this way a save/load
    round-trip (which never serialises callables) doesn't need to know about
    it at all.
    """
    mem = QuipuMemory.load(MEM_PATH, max_entries=CONFIG["max_knots"])
    if CONFIG["llm_judge"]:
        health_url = CONFIG["api_url"].rsplit("/v1/", 1)[0] + "/health"
        if llm_judge_bridge.probe(health_url, timeout=3):
            mem.bridge_classifier = bridge_needs_wide
            mem.wide_bridge = llm_judge_bridge.make_llm_judge(
                api_url=CONFIG["api_url"])
            mem.bridge_k_wide = CONFIG["bridge_k_wide"]
        else:
            # Fails visibly rather than silently: every wide-flagged query
            # would otherwise sit on a 90s timeout against a dead server
            # before pack()'s own try/except caught it. Checked once, here,
            # instead of once per turn.
            print("[qontext] LLM judge enabled but the server didn't answer "
                  "%s at startup -- hard queries get plain retrieval only "
                  "until it's reachable and the agent is restarted." %
                  health_url)
    return mem


def save_memory(mem):
    """Atomic — a crash mid-write cannot leave a half-written memory."""
    try:
        mem.save(MEM_PATH)
    except OSError as e:
        print("! could not save memory (%s); continuing in RAM only" % e)


def build_messages(mem, history, user_msg):
    """The Qontext prompt: system + pack + recency window."""
    pack = mem.pack(user_msg, CONFIG["pack_budget"])
    system = SYSTEM
    if pack:
        system += "\n\nKnown facts:\n" + pack
    recent = history[-CONFIG["recency_window"]:]
    return [{"role": "system", "content": system}] + recent + [
        {"role": "user", "content": user_msg}]


def chat(messages):
    body = json.dumps({
        "model": "local",
        "messages": messages,
        "temperature": CONFIG["temperature"],
        "top_k": CONFIG["top_k"],
        "top_p": CONFIG["top_p"],
        "max_tokens": CONFIG["max_tokens"],
    }).encode()
    req = urllib.request.Request(CONFIG["api_url"], data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read())
    return (data["choices"][0]["message"]["content"],
            data.get("usage", {}).get("prompt_tokens", 0))


def full_history_estimate(history, user_msg):
    """Approx tokens a full-history prompt would need (chars/4)."""
    chars = len(SYSTEM) + len(user_msg) + sum(len(m["content"]) for m in history)
    return chars // 4


def turn_footer(sent_tokens, history, user_msg, mem):
    est_full = full_history_estimate(history, user_msg)
    saved = 100 * (1 - sent_tokens / est_full) if est_full > sent_tokens > 0 else 0
    return ("[qontext] sent %d tokens | full history ~%d | saved ~%.0f%% | "
            "%d knots" % (sent_tokens, est_full, saved, len(mem.entries())))


def main():
    mem = load_memory()
    history = []          # full transcript, for comparison only - never sent
    n_known = len(mem.entries())
    print("Qontext live agent. %d knots remembered%s."
          % (n_known, " from previous sessions" if n_known else ""))
    print("Commands: /mem  /stats  /why <question>  /forget [text]  /quit")

    while True:
        try:
            user_msg = input("\nyou > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_msg:
            continue
        if user_msg == "/quit":
            break
        if user_msg == "/mem":
            for e in mem.entries() or ["(no knots yet)"]:
                print("  -", e)
            continue
        if user_msg == "/stats":
            st = mem.stats()
            print("  observed: %d chars | stored: %d chars (%.0f%%) | knots: %d"
                  % (st["observed_chars"], st["stored_chars"],
                     100.0 * st["stored_chars"] / max(1, st["observed_chars"]),
                     len(mem.entries())))
            print("  memory file: %s (%d bytes)"
                  % (MEM_PATH.name, MEM_PATH.stat().st_size if MEM_PATH.is_file() else 0))
            continue
        if user_msg.startswith("/forget"):
            what = user_msg[len("/forget"):].strip()
            if what:
                gone = mem.forget(what)
                print("  forgot %d knot%s matching %r."
                      % (gone, "" if gone == 1 else "s", what))
            else:
                mem.clear()
                print("  memory wiped.")
            save_memory(mem)
            continue
        if user_msg.startswith("/why"):
            question = user_msg[len("/why"):].strip()
            if not question:
                print("  usage: /why <question> — shows how knots were ranked")
                continue
            for score, in_pack, text in mem.explain(question,
                                                    CONFIG["pack_budget"])[:8]:
                print("  %5.2f %s %s" % (score, "->" if in_pack else "  ", text))
            continue

        messages = build_messages(mem, history, user_msg)
        try:
            reply, sent_tokens = chat(messages)
        except Exception as e:
            print("! server error: %s" % e)
            continue

        print("\nbot > " + reply.strip())

        # observe every turn, then persist
        mem.observe("user", user_msg)
        mem.observe("assistant", reply)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": reply})
        save_memory(mem)

        print(turn_footer(sent_tokens, history, user_msg, mem))


# ---------------------------------------------------------------- self-test

def selftest():
    mem = QuipuMemory()
    history = []
    script = [
        ("Hi! People call me Marta and I work as a nurse.", "Nice to meet you, Marta!"),
        ("My dog is called Bikkel. The demo is on Friday at 10:00.", "Noted!"),
        ("What's the weather like?", "I don't have live weather data."),
        ("Filler chat about football and rain, nothing important.", "Ha, indeed."),
    ]
    for user_msg, fake_reply in script:
        msgs = build_messages(mem, history, user_msg)
        assert msgs[0]["role"] == "system" and msgs[-1]["content"] == user_msg
        assert len(msgs) <= 2 + CONFIG["recency_window"]
        mem.observe("user", user_msg)
        mem.observe("assistant", fake_reply)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": fake_reply})

    # facts must surface in packs for later, unrelated-turn questions
    for query, needle in [("what is my dog's name?", "bikkel"),
                          ("when is the demo?", "friday"),
                          ("what is my job?", "nurse"),
                          ("what is my name?", "marta")]:
        pack = mem.pack(query, CONFIG["pack_budget"])
        assert needle in pack.lower(), (query, pack)
        sysmsg = build_messages(mem, history, query)[0]["content"]
        assert needle in sysmsg.lower()

    # persistence round-trip, without touching a real memory file
    global MEM_PATH
    real_path, tmpdir = MEM_PATH, tempfile.mkdtemp()
    try:
        MEM_PATH = Path(tmpdir) / "qontext.qx"
        save_memory(mem)
        assert load_memory().entries() == mem.entries()

        # a corrupt memory file must not take the session down
        MEM_PATH.write_bytes(b"\x00 not json {{{")
        assert load_memory().entries() == []

        # forget removes only what was asked for
        mem.forget("bikkel")
        assert not any("Bikkel" in e for e in mem.entries())
        assert any("nurse" in e for e in mem.entries())
    finally:
        for leftover in Path(tmpdir).iterdir():
            leftover.unlink()
        os.rmdir(tmpdir)
        MEM_PATH = real_path

    # prompt stays small as history grows
    for i in range(30):
        history.append({"role": "user", "content": "filler message %d" % i})
    msgs = build_messages(mem, history, "hello again")
    assert len(msgs) == 2 + CONFIG["recency_window"]
    print("selftest OK: pack injection, recall, persistence, corrupt-file "
          "recovery, selective forget, bounded prompt")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
