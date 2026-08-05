#!/usr/bin/env python3
"""The retracted bridges, re-run against a benchmark with a written key.

Six mechanisms were built to connect a conversational turn to the fact its
reply needs, and all six were scored on `rp_turnbench.py` -- a benchmark since
retracted for marking 97% as many facts "needed" when handed a reply from an
unrelated conversation. Each bridge was therefore measured on its ability to
retrieve a largely arbitrary set. Those results are uninterpretable, not
refuted, and this is the re-run they need.

`turn_bench.py` supplies what was missing: the fact, the turn that needs it,
the words that prove it was carried, and a label for the KIND of gap between
them. A bridge that crosses hypernym gaps but not inference gaps now shows up
as exactly that, instead of as a single percentage of an arbitrary population.

How a bridge is given its chance. The pack is filled lexically first, then the
bridge's proposals are added while budget remains. This is deliberately
generous -- the bridge can only ever add facts, never displace one the
ranking wanted -- so a bridge that fails here fails on reach, which is the
thing it exists to provide.

THE CONTROL GATES EVERY ARM. Each arm is scored against shuffled keys before
its accuracy is printed, and an arm that cannot separate the right fact from
an arbitrary one is reported as FAILED with no score. Pure coverage packing
already fails this way at 0.7x; it is not a hypothetical.

    python bridge_bench.py
    python bridge_bench.py --budget 800 --topk 10
"""

import argparse
import functools
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = HERE.parent / "qontext-live"
if not LIVE.is_dir():
    # qontext-memory/bench layout: qontext_memory.py sits at the repo root
    # instead of a sibling qontext-live/ folder.
    LIVE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(LIVE))

import qontext_memory as qm                                    # noqa: E402
import turn_bench as tb                                        # noqa: E402

MIN_SEPARATION = 5.0
KINDS = ["quiz", "hypernym", "reference", "script", "consequence", "inference"]


# ----------------------------------------------------------------- bridges

def bridge_none(_mem):
    return None


def bridge_affordance(mem, filename="affordance_web.json"):
    """The model-generated affordance web: what a word can apply to."""
    sys.path.insert(0, str(LIVE))
    from build_affordance_web import AffordanceWeb
    path = LIVE / filename
    if not path.exists():
        return "missing %s" % filename
    web = AffordanceWeb.load(path)

    def propose(query, knots, topk):
        want = set()
        for word in qm._words(query):
            for other, _w in web.related(word, limit=8):
                want.add(other)
        if not want:
            return []
        scored = []
        for knot in knots:
            overlap = len(want & {qm._stem(w) for w in qm._words(knot)})
            if overlap:
                scored.append((overlap, knot))
        scored.sort(key=lambda p: -p[0])
        return [k for _s, k in scored[:topk]]

    propose.coverage = web
    return propose


_WEAVE_CACHE = {}


def _pretrained_weave(filename="weave.qw"):
    if filename not in _WEAVE_CACHE:
        sys.path.insert(0, str(LIVE))
        from qontext_weave import WordWeave
        path = LIVE / filename
        _WEAVE_CACHE[filename] = (WordWeave.load(path) if path.exists()
                                  else None)
    return _WEAVE_CACHE[filename]


def _weave_propose(weave, tokenize):
    """Shared by both weave arms. `tokenize` is the weave's own tokenizer,
    NOT qm._words/_stem -- the weave's vocabulary is raw lowercase words,
    unstemmed, built by its own _tokens(). Looking it up with qm's stemmer
    would silently miss almost everything through a tokenization mismatch
    that has nothing to do with whether the mechanism works."""
    def propose(query, knots, topk):
        want = set()
        for word in tokenize(query):
            for other, _s in weave.related(word, limit=8, minimum=0.20):
                want.add(other)
        if not want:
            return []
        scored = []
        for knot in knots:
            klow = knot.lower()
            overlap = sum(1 for w in want if w in klow)
            if overlap:
                scored.append((overlap, knot))
        scored.sort(key=lambda p: -p[0])
        return [k for _s, k in scored[:topk]]
    return propose


def bridge_weave_suite(mem):
    """The co-occurrence weave, trained fresh on THIS suite's own stored
    knots -- the same fairness move as affordance_web (rebuilt): no excuse
    that it failed for lack of matching vocabulary, because it was built
    from exactly the vocabulary being tested. Answers: does the mechanism
    work at all, from a cold start, on a corpus this small (~170 knots)?
    RP_FINDINGS.md already flagged this exact risk once before ("mechanism
    proven, data starved") on a much larger corpus than this.
    """
    sys.path.insert(0, str(LIVE))
    from qontext_weave import WordWeave, _tokens
    weave = WordWeave()
    for knot in mem.entries():
        weave.learn(knot)
    weave.prune()
    return _weave_propose(weave, _tokens)


def bridge_weave_pretrained(mem, filename="weave.qw"):
    """The same mechanism, pre-seeded on 43.9M tokens of WikiText instead of
    this suite's own vocabulary -- the "accumulated over many sessions"
    condition the weave is actually designed for, per qontext_weave.py's own
    docstring. Encyclopedic text, not conversational, so a real risk here is
    domain mismatch rather than the mechanism itself; checked below rather
    than assumed.
    """
    sys.path.insert(0, str(LIVE))
    from qontext_weave import _tokens
    weave = _pretrained_weave(filename)
    if weave is None:
        return "missing %s" % filename
    return _weave_propose(weave, _tokens)


_EMBED_CACHE = {}


def _embedder(name, backend):
    """One encoder, cached across arms and suites so nothing reloads."""
    key = (name, backend)
    if key in _EMBED_CACHE:
        return _EMBED_CACHE[key]
    import numpy as np
    if backend == "static":
        from model2vec import StaticModel
        model = StaticModel.from_pretrained(name)

        def raw(texts):
            return model.encode(texts)
    elif backend == "onnx":
        raw = _minilm_onnx()
    else:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(name)

        def raw(texts):
            return model.encode(texts, batch_size=128,
                                show_progress_bar=False)

    cache = {}

    def embed(texts):
        fresh = [t for t in texts if t not in cache]
        if fresh:
            vecs = np.asarray(raw(fresh), dtype="float32")
            vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
            for t, v in zip(fresh, vecs):
                cache[t] = v
        return np.stack([cache[t] for t in texts])

    _EMBED_CACHE[key] = embed
    return embed


def _make_embed_bridge(name, backend):
    def make(_mem):
        try:
            import numpy as np
            embed = _embedder(name, backend)
        except Exception as exc:                      # noqa: BLE001
            return "%s unavailable (%s)" % (backend, exc)

        def propose(query, knots, topk):
            matrix = embed(list(knots))
            order = np.argsort(-(matrix @ embed([query])[0]))
            return [knots[i] for i in order[:topk]]
        return propose
    return make


def _minilm_onnx():
    """MiniLM without torch.

    sentence-transformers pulls a 526 MB torch wheel that stalls here, and
    download.pytorch.org is unreachable from this sandbox. The ONNX export of
    the same weights is 88 MB and runs on onnxruntime, so the *model* is
    identical to what sentence-transformers would load -- only the runtime
    differs. Mean pooling over the token states, then L2 normalise, which is
    exactly what all-MiniLM-L6-v2's pooling layer does.
    """
    import numpy as np
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    repo = "sentence-transformers/all-MiniLM-L6-v2"
    session = ort.InferenceSession(hf_hub_download(repo, "onnx/model.onnx"),
                                   providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(hf_hub_download(repo, "tokenizer.json"))
    tok.enable_truncation(max_length=256)
    tok.enable_padding()
    wanted = {i.name for i in session.get_inputs()}

    def raw(texts):
        out = []
        for start in range(0, len(texts), 64):
            batch = tok.encode_batch(texts[start:start + 64])
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in wanted:
                feed["token_type_ids"] = np.zeros_like(ids)
            states = session.run(None, {k: v for k, v in feed.items()
                                        if k in wanted})[0]
            m = mask[..., None].astype("float32")
            out.append((states * m).sum(1) / np.clip(m.sum(1), 1e-9, None))
        return np.concatenate(out)

    return raw


# Static vectors are a deliberate LOWER BOUND: no context window, essentially
# a well-trained bag of word vectors. A contextual encoder should do better,
# and if it does not that is worth knowing too.
bridge_static = _make_embed_bridge("minishlab/potion-base-8M", "static")
bridge_minilm = _make_embed_bridge("all-MiniLM-L6-v2 (onnx)", "onnx")


def bridge_affordance_rebuilt(mem):
    """The web regenerated over THIS benchmark's vocabulary.

    The first run scored the web at 3.7x -- a control failure -- but it had
    been generated over the roleplay logs, so the failure could have been
    coverage rather than mechanism. This arm removes that excuse: every
    surface form in the 14 turn-shaped pairs was expanded. If it still fails,
    the relation is wrong for these gaps rather than absent from the table.
    """
    return bridge_affordance(mem, "affordance_turnbench.json")


def bridge_llm_judge(mem, api_url=None, max_candidates=200, timeout=90):
    """Ask the model, at query time, which stored facts it would need.

    The mechanism itself lives in `llm_judge_bridge.py` (qontext-live/),
    shared with `qontext_memory.py`'s production `wide_bridge` wiring, so
    there is one implementation to trust rather than two that could drift.
    See that module's docstring for what this does and why, and
    RP_FINDINGS.md ("The LLM judge: the first mechanism to actually cross
    script/consequence") for the numbers.

    This wrapper only adds the bench-specific probe-and-SKIP convention:
    every other arm that can be 'missing' (affordance web, weave) reports
    SKIPPED with a reason instead of silently returning empty proposals, and
    a down server needs to look the same way, not like "the mechanism ran
    and found nothing."
    """
    sys.path.insert(0, str(LIVE))
    from llm_judge_bridge import DEFAULT_API_URL, make_llm_judge, probe
    api_url = api_url or DEFAULT_API_URL
    if not probe(api_url.rsplit("/v1/", 1)[0] + "/health"):
        return ("llama server not reachable at %s "
                "(start it, --reasoning-budget 0)" % api_url)
    return make_llm_judge(api_url=api_url, max_candidates=max_candidates,
                          timeout=timeout)


BRIDGES = [
    ("baseline (lexical only)", bridge_none, {}),
    ("write-time index terms", bridge_none, {"INDEX_TERMS": 10}),
    ("index terms OFF", bridge_none, {"INDEX_TERMS": 0}),
    ("affordance web (rebuilt)", bridge_affordance_rebuilt, {}),
    ("weave (this suite)", bridge_weave_suite, {}),
    ("weave (WikiText, pretrained)", bridge_weave_pretrained, {}),
    ("static emb (model2vec)", bridge_static, {}),
    ("contextual emb (MiniLM)", bridge_minilm, {}),
    ("llm judge (query-time)", bridge_llm_judge, {}),
]


# ------------------------------------------------------------------ harness

def packer(mem, propose, topk, candidates_fn=None):
    """Lexical pack first, then the bridge's proposals while budget remains.

    `candidates_fn(mem) -> [knot text]` overrides what the bridge is shown
    in place of the default `mem.entries()` (insertion order, unbounded).
    Only the llm-judge arms pass one -- see `_llm_judge_variant` -- so
    every other arm's already-measured numbers are untouched by this.
    """
    def pack(query, budget):
        base = mem.pack(query, budget)
        if propose is None:
            return base
        used = len(base)
        chosen = list(base.split("\n")) if base else []
        pool = candidates_fn(mem) if candidates_fn else mem.entries()
        for knot in propose(query, pool, topk):
            if knot in chosen:
                continue
            cost = len(knot) + (1 if chosen else 0)
            if used + cost > budget:
                continue
            chosen.append(knot)
            used += cost
        return "\n".join(chosen)
    return pack


def score(pack, budget, key_for, pairs=None, mem=None):
    """Items whose fact never reached the store are skipped -- an extraction
    failure is not a retrieval failure, and scoring it as one blames the
    retriever for something it never had."""
    rows = tb.PAIRS if pairs is None else pairs
    store = "\n".join(mem.entries()).lower() if mem is not None else None
    hits, by_kind = 0, {}
    for i, (_st, _kw, query, kind) in enumerate(rows):
        if store is not None and not any(k in store for k in rows[i][1]):
            continue
        packed = pack(query, budget).lower()
        got = any(k in packed for k in key_for(i))
        hits += got
        slot = by_kind.setdefault(kind, [0, 0])
        slot[0] += got
        slot[1] += 1
    return hits, by_kind


def control(pack, budget, seeds, pairs=None, mem=None):
    rows = tb.PAIRS if pairs is None else pairs
    out = []
    for seed in seeds:
        rnd = random.Random(seed)

        def wrong(i, _r=rnd):
            other = [j for j in range(len(rows)) if j != i]
            return rows[_r.choice(other)][1]
        out.append(score(pack, budget, wrong, rows, mem)[0])
    return statistics.mean(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=800)
    ap.add_argument("--budget", type=int, default=800)
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--control-seeds", type=int, default=8)
    ap.add_argument("--llm-judge-candidates", default="200",
                    help="comma-separated max_candidates values to sweep "
                         "for the llm judge arm, e.g. 50,100,200. The "
                         "candidate list is the expensive part of that "
                         "call (see llm_judge_bridge.py) -- this is what "
                         "lets you trade recall for latency with the model "
                         "held fixed, instead of swapping models. Default "
                         "reproduces the single arm exactly as before.")
    args = ap.parse_args()

    conv_seeds = [7, 23, 99][:args.seeds]
    ctrl_seeds = list(range(1, args.control_seeds + 1))

    # One row per pool size. With the default single value this is
    # byte-for-byte the static BRIDGES list that shipped before this sweep
    # existed -- same label, same behaviour.
    candidate_sizes = [int(n) for n in
                       args.llm_judge_candidates.split(",") if n.strip()]
    bridges = [b for b in BRIDGES if not b[0].startswith("llm judge")]
    for n in candidate_sizes:
        label = ("llm judge (query-time)" if len(candidate_sizes) == 1
                 else "llm judge (pool=%d)" % n)
        made = functools.partial(bridge_llm_judge, max_candidates=n)
        # Measure the same importance+recency candidate selection
        # production uses (QontextMemory.candidates()), not the raw
        # insertion-order entries() every other arm gets -- otherwise this
        # sweep would be testing a selection nothing in production
        # actually uses. See RP_FINDINGS.md, the candidate-pool sweep
        # entries, for why that distinction mattered here specifically.
        made.candidates_fn = lambda mem, n=n: mem.candidates(n)
        bridges.append((label, made, {}))

    print("bridge_bench: budget %d, top-%d proposals, %d conversation seeds"
          % (args.budget, args.topk, len(conv_seeds)))
    print("every arm is gated by the shuffle control at %.1fx\n"
          % MIN_SEPARATION)

    # `real` and `shuffled` are printed even when the control fails, because
    # a failure has two very different causes and the gate alone cannot tell
    # them apart:
    #
    #   real flat, shuffled up   -> the bridge added bulk, not signal. The
    #                               pack got bigger and caught wrong keys.
    #   real up, shuffled up more -> the bridge found something and drowned it.
    #
    # Reporting only the verdict would repeat the original sin of this
    # project: a number whose failure mode is invisible.
    header = "%-26s   %-24s %-24s" % ("arm", "A (tuned-on)", "B (HELD OUT)")
    print(header)
    print("-" * len(header))

    original = {k: getattr(qm, k) for k in ("INDEX_TERMS",)}
    rows = []
    for label, make, overrides in bridges:
        for key, value in overrides.items():
            setattr(qm, key, value)
        for key, value in original.items():
            if key not in overrides:
                setattr(qm, key, value)

        problem = None
        per_suite = {}
        diag_calls, diag_net, diag_errs, diag_trunc, diag_secs = (
            0, 0, [], False, 0.0)
        for sname, spairs in tb.SUITES:
            turn_hits, turn_all, seps, kinds = 0, 0, [], {}
            for cseed in conv_seeds:
                conv = tb.build(args.turns, cseed, 0.10, "daily-clean",
                                spairs)
                mem = tb.memory(conv)
                propose = make(mem)
                if isinstance(propose, str):
                    problem = propose
                    break
                pack = packer(mem, propose, args.topk,
                              candidates_fn=getattr(make, "candidates_fn",
                                                    None))
                real, by_kind = score(pack, args.budget,
                                      lambda i: spairs[i][1], spairs, mem)
                shuf = control(pack, args.budget, ctrl_seeds, spairs, mem)
                seps.append(real / shuf if shuf else float("inf"))
                for kind, (got, total) in by_kind.items():
                    slot = kinds.setdefault(kind, [0, 0])
                    slot[0] += got
                    slot[1] += total
                    if kind != "quiz":
                        turn_hits += got
                        turn_all += total
                # Any instrument that talks to something outside the process
                # (the LLM judge, so far) must be able to fail visibly. A
                # server error and "the model said nothing relevant" both
                # collapse to an empty proposal list -- this is the only
                # thing that tells them apart.
                diag_calls += getattr(propose, "calls", 0)
                diag_net += getattr(propose, "network_calls", 0)
                diag_errs += getattr(propose, "errors", [])
                diag_trunc = diag_trunc or getattr(propose, "truncated", False)
                diag_secs += getattr(propose, "seconds", 0.0)
            if problem:
                break
            per_suite[sname] = (turn_hits, turn_all, min(seps), kinds)

        if problem:
            print("%-26s  SKIPPED: %s" % (label, problem))
            continue

        cells, worst = [], 99.9
        for sname, _sp in tb.SUITES:
            hits, total, sep, kinds = per_suite[sname]
            worst = min(worst, sep)
            cells.append((sname, hits, total, sep, kinds))

        line = "%-26s" % label
        failed = False
        for sname, hits, total, sep, _k in cells:
            if sep < MIN_SEPARATION:
                line += "   %s FAILED(%.1fx)" % (sname.split()[0], sep)
                failed = True
            else:
                line += "   %s %2d/%-2d (%2.0f%%) %4.1fx" % (
                    sname.split()[0], hits, total, 100.0 * hits / total, sep)
        print(line)
        if diag_calls:
            note = ("%d errors, e.g. %s" % (len(diag_errs), diag_errs[0])
                    if diag_errs else "0 errors")
            avg_ms = 1000.0 * diag_secs / diag_net if diag_net else 0.0
            print("    %d proposal calls, %d actual server round-trips "
                  "(rest cached), %s%s" % (
                      diag_calls, diag_net, note,
                      " -- pool truncated at max_candidates" if diag_trunc
                      else ""))
            print("    %.1fs total in real network calls, %.0fms avg per "
                  "round-trip -- this is the number a smaller model or a "
                  "smaller candidate pool is supposed to move" % (
                      diag_secs, avg_ms))
        rows.append((label, failed, cells))

    for key, value in original.items():
        setattr(qm, key, value)

    base = next((r for r in rows if r[0].startswith("baseline")), None)
    if base and not base[1]:
        print("\nchange against baseline, per suite "
              "(a gain on A alone is overfitting, not a finding):")
        bA, bB = base[2][0][1], base[2][1][1]
        for label, failed, cells in rows:
            if failed or label.startswith("baseline"):
                continue
            dA, dB = cells[0][1] - bA, cells[1][1] - bB
            verdict = ("generalises" if dA > 0 and dB > 0 else
                       "A only -- OVERFIT" if dA > 0 >= dB else
                       "B only" if dB > 0 >= dA else "no effect")
            print("  %-26s A %+d   B %+d   %s" % (label, dA, dB, verdict))

    print("\nper gap kind, held-out suite B:")
    for label, failed, cells in rows:
        if failed:
            continue
        kinds = cells[1][4]
        print("  %-26s %s" % (label, "  ".join(
            "%s %d/%d" % (k, kinds[k][0], kinds[k][1])
            for k in KINDS if k in kinds and k != "quiz")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
