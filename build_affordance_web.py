#!/usr/bin/env python3
"""Build an affordance web by asking a model what a word can apply to.

Five bridges have failed on the same population, and all five encoded the same
relation: *similarity*. PPMI encodes "appears near"; embeddings encode "appears
in similar contexts". Neither answers the question the failing retrievals
actually pose.

    query:  "what did she cancel?"
    knot:   "didn't you have class though?"

The link needed is not that `cancel` resembles `class`. It is that a class is
the *kind of thing one cancels* — an affordance, typed and directional. No
similarity model computes it, because it is not a property of the pair.

This asks a model instead, once, offline, and writes a lookup table. The
result is still a table rather than comprehension; the wager is that a table
of the right relation beats a model of the wrong one.

    python build_affordance_web.py --out affordance_web.json
    python build_affordance_web.py --vocab 200 --resume

Resumable: the file is rewritten after every word, so an interrupted run
continues where it stopped. Costs one short generation per word.

Then measure it against the same bar every other bridge was held to:

    python bridge_ceiling.py --web affordance_web.json
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qontext_memory import STOP, _stem, _words  # noqa: E402
# rp_probe reads the private roleplay logs (see its own docstring) and isn't
# part of the public repo -- imported lazily, inside vocabulary() below,
# which is the only thing that needs it. Loading a pre-built web from JSON
# (bridge_bench.py's actual use of this module) never touches it, and
# shouldn't be made to depend on a module that only exists in one tree.

API_URL = "http://127.0.0.1:8080/v1/chat/completions"
EXCLUDED = {"log8.txt", "log12.txt"}

# Why this uses the raw completion endpoint and not the chat endpoint.
#
# The 12B emits reasoning tokens for everything, and the reasoning *scales
# with the task*: one word drew 3,078 characters of thinking, fifteen words
# drew 13,644, and neither ever reached an answer inside any affordable cap.
# Batching amortised nothing because there was nothing to amortise -- the
# model simply thinks proportionally harder.
#
# A raw completion has no chat template, so there is no thinking block to
# enter. The model continues a pattern instead of being asked a question, and
# a few-shot prefix makes the pattern unambiguous. Cost: ~40 tokens per word
# instead of thousands.
_WORD = re.compile(r"^[a-z][a-z-]{2,}$")
COMPLETION_URL = "http://127.0.0.1:8080/completion"

FEWSHOT = """The following lists, for each word, everyday things that the word
applies to, acts on, is part of, or is done with. Never synonyms.

cancel: class, meeting, appointment, flight, subscription, order, plan
harbour: ship, boat, dock, water, port, sailor, cargo
sister: brother, family, parents, sibling, wedding, childhood
wear: coat, dress, ring, shoes, uniform, jacket, glasses
promise: oath, secret, favour, marriage, debt, silence
kitchen: stove, sink, cooking, dinner, knife, table, fridge
"""


CHAT_PROMPT = (
    "List up to 10 common single words naming things that \"%s\" applies to, "
    "acts on, is part of, or is done with in everyday conversation. "
    "If it is a verb, name what one typically does it to. "
    "No synonyms, no phrases, no explanation. "
    "Answer with comma-separated lowercase words only."
)


def ask_chat(word, timeout=120, max_tokens=200):
    """The chat endpoint. Needs the server started with --reasoning-budget 0,
    otherwise the model thinks for thousands of tokens before answering."""
    body = json.dumps({
        "model": "local",
        "messages": [
            {"role": "system", "content": "You answer with comma-separated "
                                          "lowercase single words and nothing "
                                          "else."},
            {"role": "user", "content": CHAT_PROMPT % word},
        ],
        "temperature": 0.3, "top_p": 0.95, "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    request = urllib.request.Request(
        API_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as error:
        return None, type(error).__name__
    message = data["choices"][0]["message"]
    text = (message.get("content") or "").strip()
    if not text:
        thinking = message.get("reasoning_content") or ""
        return None, ("empty (finish=%s, %d chars of reasoning) — start the "
                      "server with --reasoning-budget 0"
                      % (data["choices"][0].get("finish_reason"),
                         len(thinking)))
    return text, None


def ask(word, timeout=120, max_tokens=64):
    """One raw completion. Returns (text, problem)."""
    body = json.dumps({
        "prompt": FEWSHOT + "%s:" % word,
        "n_predict": max_tokens,
        "temperature": 0.3,
        "top_p": 0.95,
        "stop": ["\n", "\r\n"],
        "cache_prompt": True,
    }).encode()
    request = urllib.request.Request(
        COMPLETION_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as error:
        detail = ""
        if isinstance(error, urllib.error.HTTPError):
            try:
                detail = error.read().decode("utf-8", "replace")[:120]
            except Exception:
                pass
        return None, "%s %s" % (type(error).__name__, detail)
    text = (data.get("content") or "").strip()
    if not text:
        return None, "empty"
    return text, None


def parse(text, source):
    """A completed line -> stems, minus noise, stop words and the word itself."""
    out, seen = [], {_stem(source)}
    for piece in re.split(r"[,\n;]+", text.lower()):
        piece = piece.strip().strip(".:-*0123456789 ")
        if " " in piece or not _WORD.match(piece) or piece in STOP:
            continue
        stem = _stem(piece)
        if stem in seen or len(stem) < 3:
            continue
        seen.add(stem)
        out.append(stem)
    return out[:12]


def vocabulary(root, limit):
    """The words queries are actually made of, commonest first.

    Expanding the whole dictionary would be wasted generation: only words that
    appear in a user's turn can ever trigger an expansion at retrieval time.
    """
    from rp_probe import load
    counts = Counter()
    for path in sorted(Path(root).glob("*.txt")):
        if path.name in EXCLUDED:
            continue
        try:
            turns = load(path)
        except Exception:
            continue
        for _speaker, is_user, text in turns:
            if is_user:
                counts.update(_words(text))
    return [w for w, _n in counts.most_common() if len(w) >= 3][:limit]


class AffordanceWeb:
    """A weave-compatible lookup table, so it drops into existing code.

    Same interface as qontext_weave.WordWeave: `related` and `expand`. That
    means it can be handed to QontextMemory(weave=...) and measured by
    bridge_ceiling.py without either of them knowing the difference.
    """

    def __init__(self, links=None):
        self.links = links or {}

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data.get("links", data))

    def save(self, path):
        Path(path).write_text(json.dumps({"links": self.links}, indent=0,
                                         sort_keys=True), encoding="utf-8")

    def related(self, word, limit=None, minimum=0.0):
        # Rank is the model's own ordering; the first named is the most
        # typical. Weight decays with position so the tail counts for less.
        got = self.links.get(_stem(word), [])
        rows = [(w, round(1.0 - 0.06 * i, 3)) for i, w in enumerate(got)]
        rows = [r for r in rows if r[1] >= minimum]
        return rows[:limit] if limit else rows

    def expand(self, words, limit=6, minimum=0.20):
        out = []
        for word in words:
            for other, weight in self.related(word, limit=limit,
                                              minimum=minimum):
                out.append(other)
        return out

    def __len__(self):
        return len(self.links)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", default=r"C:\Users\hylke\Documents\RP_Logs")
    ap.add_argument("--out", default="affordance_web.json")
    ap.add_argument("--vocab", type=int, default=400,
                    help="how many of the commonest query words to expand")
    ap.add_argument("--vocab-file",
                    help="expand this word list instead of mining the logs "
                         "(one word per line, '#' comments ignored). Used to "
                         "give the web the vocabulary of a specific "
                         "benchmark, so a failure is mechanism rather than "
                         "coverage.")
    ap.add_argument("--resume", action="store_true",
                    help="keep whatever is already in --out and continue")
    ap.add_argument("--max-tokens", type=int, default=200,
                    help="tokens per word")
    ap.add_argument("--endpoint", choices=("chat", "completion"),
                    default="chat",
                    help="chat needs --reasoning-budget 0 on the server; "
                         "completion needs a base-ish model")
    args = ap.parse_args()

    if args.vocab_file:
        source = Path(args.vocab_file)
        words = [w.strip().lower() for w in
                 source.read_text(encoding="utf-8").splitlines()
                 if w.strip() and not w.startswith("#")]
        print("vocabulary from: %s" % source)
    else:
        root = Path(args.logs)
        if not root.is_dir():
            for guess in (Path("/sessions/admiring-sharp-keller/mnt/RP_Logs"),
                          Path.home() / "Documents" / "RP_Logs"):
                if guess.is_dir():
                    root = guess
                    break
        words = vocabulary(root, args.vocab)
        print("logs: %s" % root)
    print("vocabulary: %d words to expand" % len(words))

    out = Path(args.out)
    links = {}
    if args.resume and out.exists():
        links = AffordanceWeb.load(out).links
        print("resuming: %d already done" % len(links))

    todo = [w for w in words if _stem(w) not in links]
    started = time.time()
    failures = 0
    for i, word in enumerate(todo, 1):
        speak = ask_chat if args.endpoint == "chat" else ask
        text, problem = speak(word, max_tokens=args.max_tokens)
        if problem:
            failures += 1
            print("  %-16s failed: %s" % (word, problem))
            if failures > 15:
                print("giving up: too many failures, is the server running?")
                break
            continue
        got = parse(text, word)
        if got:
            # Prompt with the surface form, key by the stem.
            #
            # These were the same string in the first build, because the
            # vocabulary came from _words(), which stems. So the model was
            # asked "what does `amaz` apply to?" -- and, worse, any word whose
            # stem differs from its surface form was stored under a key that
            # related() would never look up, since related() stems its
            # argument. The entry existed and was unreachable.
            links[_stem(word)] = got
            AffordanceWeb(links).save(out)
        if i % 25 == 0 or i <= 3:
            rate = i / max(1e-9, time.time() - started)
            print("  %4d/%-4d  %-14s -> %s   (%.1f words/s)"
                  % (i, len(todo), word, ", ".join(got[:6]), rate))

    print("\nwrote %s: %d words linked" % (out, len(links)))
    for probe in ("cancel", "class", "meet", "promis", "wear", "sister"):
        rows = AffordanceWeb(links).related(probe, limit=8)
        if rows:
            print("  %-8s -> %s" % (probe, ", ".join(w for w, _s in rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
