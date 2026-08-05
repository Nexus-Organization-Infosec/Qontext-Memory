"""LLM-judge-at-query-time: ask the model directly which stored facts it
needs, instead of encoding some notion of relatedness into a table
beforehand.

Every other bridge in this project encodes similarity -- PPMI co-occurrence,
static and contextual embeddings, an affordance web built by asking a model
offline what a word applies to, cheap reranking of an embedding shortlist.
All of them failed identically on two gap kinds, script and consequence,
because those items are constructed to share no vocabulary with their
query, and none of those mechanisms has anything to find in a case like
that by design.

This bridge does not precompute anything. At query time it lists every
candidate fact and the query and asks the model which facts it would need
to respond sensibly -- including facts only implied by what happens next.
It is deliberately NOT pre-filtered by any lexical or embedding shortlist;
a shortlist would silently discard exactly the zero-overlap items this
mechanism exists to catch.

Measured in `qontext-bench/bridge_bench.py` against `turn_bench.py`'s
suites (RP_FINDINGS.md, "The LLM judge: the first mechanism to actually
cross script/consequence"): suite B 72% real / 12.4x control, suite A 83% /
10.7-11.6x -- both comfortably clear the 5.0x bar. Per gap kind on B:
hypernym 11/12 (was 0), script 7/12 (was 0), consequence 6/12 (was 3),
inference 5/9 (was 0). Verified 0 errors across 123 real server round-trips
on the run those numbers come from.

The real cost, not hidden: seconds per uncached query, not microseconds
like every other bridge here. That is why `qontext_memory.py` never calls
this on every turn -- see `QontextMemory`'s `wide_bridge` parameter, gated
by `bridge_needs_wide()`, so the LLM is only asked on the minority of
queries lexical retrieval is already predicted to miss.

Needs the server started with `--reasoning-budget 0` (or thinking disabled
via chat_template_kwargs, set below regardless) -- see
build_affordance_web.py's docstring for why: the chat endpoint's reasoning
scales with task size, and an unbounded think block here costs thousands of
tokens per query for nothing gained on a judge task this narrow.

    from llm_judge_bridge import make_llm_judge, probe
    if probe():
        judge = make_llm_judge()
        mem = QontextMemory(bridge_classifier=bridge_needs_wide,
                            wide_bridge=judge, bridge_k_wide=10)
"""

import json
import time
import urllib.error
import urllib.request

DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8080/health"


def probe(health_url=DEFAULT_HEALTH_URL, timeout=5):
    """Fail visibly, not silently. A server that's down must look different
    from a mechanism that ran and found nothing -- checked once, up front,
    rather than discovered turn by turn as every query quietly returns []."""
    try:
        with urllib.request.urlopen(health_url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def make_llm_judge(api_url=DEFAULT_API_URL, max_candidates=200, timeout=90):
    """Returns propose(query, knots, topk) -> [knot text, ...], the same
    shape every bridge in this project uses (see BRIDGE_K in
    qontext_memory.py).

    The returned callable carries four attributes for instrumentation,
    since a bridge that talks to something outside the process must be
    checkable, not just trusted:
      .calls          every propose() invocation, cached or not
      .network_calls  actual server round-trips (repeats of the same query
                       against the same candidate pool are cached)
      .errors         one entry per unique failure (HTTP/network error, or
                       an empty response -- usually a reasoning-budget
                       problem on the server, see the message text)
      .truncated      True if a candidate pool ever exceeded max_candidates
                       and was cut, silently, without this flag
      .seconds        wall-clock time spent inside real network calls (not
                       cached replays) -- the actual thing "smaller model /
                       fewer candidates" is supposed to move. A model swap
                       measured only on accuracy and never on this is an
                       instrument given no chance to prove the thing it was
                       tried for.
    """
    cache = {}

    def ask(query, candidates):
        key = (query, candidates)
        if key in cache:
            return cache[key]
        propose.network_calls += 1
        started = time.perf_counter()
        listing = "\n".join("%d. %s" % (i + 1, c)
                            for i, c in enumerate(candidates))
        prompt = (
            "Below is a numbered list of facts and a line from a "
            "conversation. Name the numbers of the facts a speaker would "
            "need to know to respond sensibly to that line -- including "
            "facts only implied by what would happen next, not just facts "
            "that share its wording. If none apply, answer 0.\n\n"
            "FACTS:\n%s\n\nLINE: %s\n\n"
            "Answer with comma-separated numbers only, most relevant "
            "first. No explanation." % (listing, query)
        )
        body = json.dumps({
            "model": "local",
            "messages": [
                {"role": "system", "content": "You answer with "
                                              "comma-separated numbers and "
                                              "nothing else."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0, "top_p": 1.0, "max_tokens": 32,
            "chat_template_kwargs": {"enable_thinking": False},
            # The candidate list, not the answer, is the expensive part of
            # this call -- generation is a handful of numbers, but the
            # prompt re-lists every candidate fact, every time. Below
            # max_candidates total knots, the FACTS block is a stable,
            # growing PREFIX across consecutive calls (new facts only ever
            # append). Above the cap, the visible window slides with each
            # new knot instead of staying frozen -- see the recency slice
            # in propose() -- so the shared-prefix property only holds
            # between calls with no new knot learned in between, not
            # indefinitely; still the common case, since most turns don't
            # produce a new knot at all. cache_prompt lets the server reuse
            # the KV cache for whatever prefix IS shared instead of
            # reprocessing it from zero every time -- same output
            # (temperature 0, this changes nothing about what's asked or
            # answered), only how much of it has to be recomputed.
            "cache_prompt": True,
        }).encode()
        request = urllib.request.Request(
            api_url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError,
                OSError) as error:
            propose.seconds += time.perf_counter() - started
            cache[key] = ("error", type(error).__name__)
            propose.errors.append(cache[key][1])
            return cache[key]
        propose.seconds += time.perf_counter() - started
        message = data["choices"][0]["message"]
        text = (message.get("content") or "").strip()
        if not text:
            thinking = message.get("reasoning_content") or ""
            cache[key] = ("error", "empty (%d chars reasoning) -- set "
                          "--reasoning-budget 0" % len(thinking))
            propose.errors.append(cache[key][1])
            return cache[key]
        cache[key] = ("ok", text)
        return cache[key]

    def propose(query, knots, topk):
        # The most RECENT max_candidates knots, not the first -- `knots` is
        # append-ordered (qontext_memory.py's `self._knots` is a plain
        # append-only list), so a prefix slice would freeze the visible
        # window at whatever was learned first and never see anything added
        # after it, silently, for the life of the memory once it grows past
        # max_candidates. Found via bridge_bench's pool-size sweep: this
        # benchmark's synthetic conversations happen to plant target facts
        # early, so a prefix slice looked fine here and would have shipped
        # wrong regardless.
        all_knots = tuple(knots)
        pool = all_knots[-max_candidates:] if max_candidates > 0 else ()
        propose.truncated = propose.truncated or len(knots) > max_candidates
        status, payload = ask(query, pool)
        propose.calls += 1
        if status == "error":
            return []
        picks = []
        for tok in payload.replace(";", ",").split(","):
            tok = tok.strip()
            if not tok.lstrip("-").isdigit():
                continue
            n = int(tok)
            if 1 <= n <= len(pool):
                picks.append(pool[n - 1])
        seen, out = set(), []
        for p in picks:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out[:topk]

    propose.errors = []
    propose.calls = 0
    propose.network_calls = 0
    propose.truncated = False
    propose.seconds = 0.0
    return propose
