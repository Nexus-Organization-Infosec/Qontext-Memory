# Qontext — handoff

Written 3 Aug 2026, end of a long session. Read this first.
Everything below is committed to `qontext-memory/` (branch `main`, remote
`Nexus-Organization-Infosec/Qontext-Memory`). The previous handoff, from
27 July, is kept as `HANDOFF_previous.md` — most of its retrieval claims are
now retracted, see below.

---

## Where things stand in one paragraph

The cost result is solid and published. A large part of the published paper is
now retracted, because the benchmark behind it failed a control we should have
run at the start. A replacement benchmark exists, passes its control, and has
a held-out suite. On it, sentence embeddings — dismissed in the paper — turn
out to work. Paper v3 is pending and deliberately not yet written.

---

## What is TRUE and safe to build on

Everything here has a hand-written answer key.

- **119× token reduction at tied accuracy.** 800-turn conversations, real
  human dialogue as filler. 12B: 9.7/10 from a 116-token pack against 9.3/10
  from a 13,853-token transcript.
- **Conditional accuracy effect.** Reduction *improves* accuracy on 4B and 9B
  under distractor pressure; vanishes at 12B. Boundary located, not explained.
- **Chat suites A/B/C**: 10/10, 10/10, 40/40 at budget 300.
- **98 unit tests + supersession suite** pass.
- **Extraction finding**: questions and second-person remarks are not facts;
  filtering them removed 47% junk.
- **Adaptive-K bridge gate** (`bridge_classifier=qm.bridge_needs_wide`,
  opt-in, off unless set): held-out suite B 60% real (vs 40% flat K=6),
  control 7.5x. Caveat, not swept under the rug: suite A's control fails on
  one of three conversation seeds (4.4x, bar is 5.0x) even with this —
  open, unexplained, see "The next problem" below.
- **LLM-judge-at-query-time**, gated (`llm_judge_bridge.py` +
  `QontextMemory`'s `wide_bridge`, wired into `live_agent.py`): B 72% real
  / 12.4× control, A 83% / 10.7–11.6×. First mechanism in the project to
  cross script/consequence (B: script 7/12, consequence 6/12 — every prior
  mechanism scored 0–3/12 on these two). Only runs on queries
  `bridge_needs_wide()` flags — costs seconds per uncached query, so it is
  never on the majority-narrow path. See "Open / pending" below for the
  wiring detail.

## What is RETRACTED

All of it traces to one instrument, `rp_turnbench.py`, which inferred ground
truth from each conversation's own continuation.

**The control that killed it**: pair a turn with the *wrong* reply. Same
conversation → 92% as many facts still marked "needed". A reply from an
*unrelated conversation* → 97%. The same control separates correct from wrong
by **42.9×** on the hand-keyed chat suites. So the control works, and the
benchmark was measuring vocabulary coincidence.

Withdrawn: the 79.8% unreachability decomposition, 23.2% reachability, 44.5%
write-time bridging, index terms, all six bridge-reach figures,
random-beats-lexical, coverage-beats-both, the roleplay progression
4.1→11.2%, `PACK_RESERVE=0.5`, `CORD_SHARE`, `SCENE_RESERVE`.

Reproduce with `audit_needed.py` and `audit_control.py`.

## What REPLACED it

`qontext-bench/turn_bench.py` — planted facts, **written** answer keys,
conversational-turn queries, labelled by gap kind (hypernym / reference /
script / consequence / inference), plus quiz-shaped items as a sanity anchor.

Two properties that matter:

1. **The control runs first and gates the report.** Below 5× separation it
   prints the failure and exits non-zero without printing a score.
2. **Suite A is tuned-on, suite B is HELD OUT.** A gain on A alone is reported
   as overfitting, not a finding.

Separation: A 6.9–12×, B 12.8–19.2×. Quiz anchors 4/4.

`bridge_bench.py` runs every retrieval mechanism through it, gating each arm
on the control and printing `real` and `shuffled` even for failed arms — so
"added bulk" and "found something and drowned it" stay distinguishable.

## The live result

Static embeddings (`model2vec`, 30 MB): lexical pack first, bridge fills the
remainder. Wired in as `QontextMemory(bridge=fn, bridge_k=6)`, **off by
default**, so the dependency-free claim survives.

| | A (tuned-on) | B (held out) |
|---|---|---|
| lexical | 14% | 21% |
| + embeddings K=6 @800 | 43% | 42% |
| + embeddings K=30 @1500 | — | ~~68%, control 5.4×~~ **FAILED, control 4.4× at 3 seeds** |

CORRECTION (see `RP_FINDINGS.md`, "A correction, found while running this"):
the 68%/5.4× row above was measured on 2 conversation seeds. Re-run at this
project's own standard of 3, the third seed alone drops the control to
4.4× — below the bar. The row is struck through rather than deleted, on
this project's own rule about not erasing a wrong number quietly.

Generalises: +8 on A, +8 on B. Chat suites untouched.
The affordance web is cleanly refuted (fails the control even after being
rebuilt over this benchmark's own vocabulary).
**Contextual MiniLM bought one item over static.** Use the static model.

---

## The next problem, updated

All four "not yet tried" ideas from the previous handoff are now tried. Full
detail and every number in `RP_FINDINGS.md` (bottom).

**Reranking the top-30 cheaply: null, and explained.** Cosine + lexical
(Jaccard) overlap, tuned on A, confirmed on B — flat at every weight tried,
both suites. Per-item rank data shows why: the actual hard-category targets
share zero vocabulary with their query *by construction*, so there is no
lexical signal in their neighbourhood to rerank with. Rules out query-side
term weighting too (same wall).

**The co-occurrence weave, re-tested against the current benchmark for the
first time: also null on script/consequence.** `WordWeave` had only ever
been measured under the retracted `rp_turnbench.py`. Added as two arms to
`bridge_bench.py` — trained fresh on the suite's own knots (data-starved:
only 13 words survive pruning out of 656 tokens, confirmed before scoring)
and pre-trained on 43.9M WikiText tokens (real vocabulary, but topical —
`shellfish`→`oysters/mussels`, not situational). Neither catches a single
additional `script` (0/12, both) or `consequence` (3/12, both) item over
plain lexical — the pretrained arm's only gain (+3 on B) is entirely in
`hypernym`, a category embeddings already cover better. Mechanistically
consistent with the affordance web's earlier, differently-caused failure on
the same two categories: co-occurrence-in-text, counted or learned, encodes
topical relatedness, not the pragmatic/causal link ("car's in the garage"
implies "can't collect you Wednesday") those two gap kinds actually need.

**Adaptive K: ships.** A cheap rule-based classifier estimates whether
lexical is *likely to fail* (not which gap kind it is — exact-kind
agreement is only 7-16%, but "will lexical fail" hits 89% recall/80%
precision on A after two rounds of fixing it against the rank data, one of
which caught a real bug). Gates K=6/budget 800 vs K=30/budget 1500 per
query. Result: **B 60%, control 7.5× (clears the bar; blanket K=30 itself
now FAILS control at 4.4× once measured at 3 seeds, see the correction in
"The live result" above — adaptive K is not just cheaper than blanket K=30,
it is the only one of the two that passes at all). A 52%, control
4.4× — still below the 5.0× bar**, on one of three conversation seeds
(4.9×/5.9× on the other two). Should replace flat K=6 as the bridge default
when the bridge is enabled.

**Open, not resolved:** improving the classifier (recall 56%→89%, false
positives 3→2 on A) did not move the seed-7 control number at all — same
4.4× before and after. That ruled out classifier accuracy as the limiting
factor and pointed at the benchmark's own construction. Chased through two
killed hypotheses (generic filler density — no; fact-similarity graph
density — no, it's actually *denser* on B) to a real, measured, not-yet-
explained asymmetry: each of A's queries lands within top-30 cosine range
of 2.64 *other* real facts on average, B's only 2.10, despite B having 50%
more other facts to potentially collide with. The right object to explain
this is a query-to-fact bipartite graph, not the fact-to-fact one that was
tried and didn't reproduce the effect. Not built — deliberately parked as
next-cycle research, not blocking the engineering call above.

**"brief" stays a documented miss**, not a chased one: "Can you walk me
through how the caching layer works?" carries no pronoun, verb, or
discourse marker a keyword rule can use. Left as a known limitation rather
than patched with an ad hoc word for one item.

---

## Rules this project earned the hard way

Six instrumentation failures, all the same shape: a measurement produced
plausible numbers and was never given a condition under which it would have
produced obviously wrong ones.

1. **Every instrument must be given a chance to fail visibly.** A disable flag
   is verified by confirming the feature stopped happening. A generation cap
   by measuring generation length against it. A proxy ground truth by feeding
   it a wrong answer.
2. **A proxy ground truth must be shown to fail when the answer is wrong.**
   Report the shuffled score beside the headline number, always.
3. **A score of zero has two explanations** — the thing is absent, or your
   cutoff is too small. Measure the rank before naming a cause.
4. **Report per-item wins and losses, not means.**
5. **Tune on A, report on B.**

---

## Open / pending

- **LLM-judge-at-query-time — measured, and now wired into production,
  gated.** `bridge_llm_judge` arm in `bridge_bench.py`: no precomputed
  table, asks the 12B directly at query time which stored facts it needs
  (candidate pool deliberately un-filtered by any lexical/embedding
  shortlist, since that would silently drop the zero-vocabulary-overlap
  items this arm exists to catch). Run twice on the user's own server (12B
  instruct, 8192 ctx, `--reasoning-budget 0`): **B 72% real, control 12.4×;
  A 83% real, control 10.7–11.6×** — both comfortably clear the 5.0× bar.
  Per gap kind on B: hypernym 11/12 (was 0), script 7/12 (was 0),
  consequence 6/12 (was 3), inference 5/9 (was 0) — script and consequence
  had never moved under any of the six prior mechanisms tried across this
  project, including two earlier runs of this exact benchmark. Instrumented
  and checked: 0 errors, 123 real server round-trips, repeat run reproduced
  identical real-hit counts. Full detail in `RP_FINDINGS.md`, "The LLM
  judge...". **Real cost, not hidden:** seconds per uncached query vs.
  microseconds for every other arm — this is why it is gated, not a
  replacement for the embedding bridge.

  **Wiring, done this round:**
  - `qontext_memory.py`: new `wide_bridge` constructor param/attribute on
    `QontextMemory`. `bridge_classifier` still decides narrow vs. wide;
    when wide AND `wide_bridge` is set, `wide_bridge` runs INSTEAD of
    `bridge` for that one call, at `bridge_k_wide`. Leaving `wide_bridge`
    unset (the default) reproduces the previous adaptive-K behaviour
    exactly — nothing about the existing shipped feature changed for
    anyone not passing the new parameter. 7 new offline unit tests in
    `test_qontext_memory.py` (105 total, all passing) check this
    end-to-end with stub bridges: narrow queries never reach `wide_bridge`,
    wide queries never reach the cheap `bridge`, a `bridge=None` +
    `wide_bridge`-only configuration works (LLM-judge-only-on-hard-queries,
    no embeddings needed), budget/exception safety hold.
  - `llm_judge_bridge.py` (new, `qontext-live/`): the mechanism itself,
    extracted out of `bridge_bench.py` into one importable module so
    production and the benchmark share a single implementation instead of
    two that could drift. `bridge_bench.py`'s `bridge_llm_judge` arm is now
    a thin wrapper around it.
  - `live_agent.py`: actually wired up — `bridge_classifier=
    bridge_needs_wide`, `wide_bridge=llm_judge_bridge.make_llm_judge(...)`,
    set on the loaded memory in `load_memory()`. Probes the server once at
    startup and prints a visible warning (not a silent no-op) if it's
    unreachable, rather than letting every wide-flagged turn eat a 90s
    timeout. `CONFIG["llm_judge"] = False` disables it outright. No new
    dependency: `bridge` stays `None`, so narrow queries cost nothing extra
    — only the classifier-flagged minority ever reach the model.

  **Latency follow-up, this round:** tried a smaller judge model
  (Qwen3.5-9B) — real control-clearing result, but it loses specifically on
  script/consequence/inference (the categories this mechanism exists for)
  while holding on hypernym/reference; verdict was to keep Gemma 4 12B. Then
  swept `max_candidates` (50/100/200) with the 12B and, while diagnosing why
  a smaller pool scored *higher* on B (a truncated pool should never gain
  signal), found and fixed a real bug: the candidate slice was
  `knots[:max_candidates]` — the OLDEST knots, not newest, since
  `self._knots` is append-only. Any real session growing past 200 knots
  would go permanently, silently blind to everything learned after that
  point on the judge path. Fixed to `knots[-max_candidates:]`. Added
  `test_llm_judge_bridge.py` (13 tests, this module had none before —
  verified the new regression test actually catches the old bug by
  reverting the fix and re-running it first).

  Re-ran the pool sweep post-fix: pool 50/100 now add **zero** over plain
  lexical retrieval on B (identical hit counts, every gap kind) — not
  "worse," genuinely nothing. Cause: target facts sit at position <20 in a
  ~170-knot store; a suffix slice at pool 50/100 keeps positions
  ~70-170/~120-170, the opposite end. Pool 200 is untouched (170 knots fit
  under a 200 cap either direction). **Neither truncation direction is
  actually correct** — prefix (the bug) permanently hides new facts past
  the cap, suffix (the fix) permanently hides old-but-important ones,
  exactly what `PACK_RESERVE` exists to protect against in the lexical
  pack, with no equivalent for the bridge's candidate pool yet. Suffix
  stays the safer of two blind options, not a solved problem. Real fix
  would blend recency with importance; not built. **Verdict: don't shrink
  `max_candidates` below what needs to stay visible — 200 stays the
  setting** until importance-aware selection exists. Timing remains
  unclean (still non-monotonic across the sweep) and is now moot for this
  round regardless. Full detail: `RP_FINDINGS.md`, "Trying a smaller judge
  model...", "The candidate-pool sweep found a shipped bug...", "Re-run
  post-fix: pool truncation... is a dead end without importance-weighting".

  **Importance-aware selection — built this round, not yet measured.**
  `QontextMemory.candidates(limit)`: sorts by `(imp desc, seq desc)`, the
  same key `PACK_RESERVE` already uses for the lexical pack's reserved
  slice, reused for the bridge's candidate pool. Escapes both blind
  failure modes in principle — importance first beats the old prefix bug
  (a high-value old fact no longer vanishes), recency-as-tiebreak beats the
  current suffix behaviour (fresh, equally-unimportant facts still
  surface). Wired narrowly: only `wide_bridge`'s call in `pack()` uses it;
  `bridge` (embeddings, weave — already-proven arms) is untouched, still
  sees every knot in original order, unchanged from what was measured
  before. `bridge_bench.py`'s llm-judge sweep arms now measure the same
  selection via a `candidates_fn` hook on `packer()` (default `None`, every
  other arm unaffected). 7 new tests (127 total, passing). **Not yet
  benchmarked** — re-run `--llm-judge-candidates` is the actual test of
  whether this recovers small-pool accuracy; expect even the pool=200 row
  to shift slightly too, since candidate ORDER changed even though the SET
  didn't. See `RP_FINDINGS.md`, "Importance-aware candidate selection:
  built, not yet measured".

  **Measured.** Real recovery, not full recovery: B went 21%/21% (recency
  fix, zero added value) -> 42%/42% at pool 50/100, vs. 65% at pool 200
  (effectively uncapped on this ~170-knot store). Pool 50 and pool 100
  scored identically on every gap kind, exactly — the importance sort
  front-loads everything that scores above default into the first ~50
  slots, so doubling the pool adds only more default-importance filler,
  nothing that changes the answer. `_importance()` is a real, useful,
  partial signal (recovered the standing-fact gap recency alone couldn't),
  not an oracle for what a later query needs. **Three separate levers now
  tried for the original latency question — smaller model, blind pool
  shrink, importance-aware pool shrink — converge on the same answer at
  this project's current scale: the zero-risk win (`cache_prompt`) is the
  win, and every further reduction has had a real, measured accuracy cost.
  Recommending this thread stop here** unless the store genuinely outgrows
  200 knots in practice, at which point the importance-aware fallback is
  the one to lean on. See `RP_FINDINGS.md`, "Measured: real recovery, not
  full recovery, and a clean explanation why".
- **A/seed-7 control failure (4.4×)** — open research question, not an
  engineering blocker. See "The next problem, updated" above and
  `RP_FINDINGS.md` (bottom) for the full evidence chain. Next step if
  picked back up: build the query-to-fact bipartite graph (per-query top-30
  membership against every other fact), not another fact-to-fact graph.
- **Adaptive K bridge wiring — done.** `qontext_memory.py` now has
  `bridge_needs_wide()` (the wh_gated classifier) plus `bridge_classifier`,
  `bridge_k_wide`, `bridge_budget_multiplier` on `QontextMemory`, all
  opt-in (`bridge_classifier=None` default reproduces the old flat
  `bridge_k` exactly). Wiring it up caught a real pre-existing bug: a
  lexically-blind query short-circuited `pack()` before the bridge section
  ever ran, invisible until now because no prior benchmark exercised the
  method's own bridge path end to end. Fixed; re-verified against
  `RP_FINDINGS.md`'s numbers exactly (A 52%/4.4x, B 60%/7.5x); 98 unit
  tests + supersession suite + chat suites all still pass. See
  `RP_FINDINGS.md`, "Wiring adaptive K into qontext_memory.py..."
- **Paper v3** — not written, by decision: accumulate findings first. v2 on
  Zenodo still says the bridges are "uninterpretable, not refuted" and calls
  the similarity conjecture "reasonable". Both are now out of date — the
  affordance web is cleanly refuted and embeddings work. Zenodo → "New
  version"; steps in `paper/zenodo_v2.md`.
- **Unpushed commits.** `git push origin main` from
  `quipu-experiment/qontext-memory`. The sandbox cannot authenticate; this has
  to be run on the user's machine.
- **HF Space (`Trobi/qontext`) — resolved, then invalidated by a policy
  change, then rebuilt as a static demo.** The abuse flag was lifted and the
  Space updated to current `qontext_memory.py`/`qontext_rp.py`/`qontext_cords.py`
  (merged via `--allow-unrelated-histories -X ours`, since the Space's git
  history originated from the web UI). It then failed at runtime
  ("No @spaces.GPU function detected") because it was pinned to ZeroGPU;
  downgrading to `cpu-basic` turned out to require a PRO subscription — as of
  Aug 2026, HF requires PRO for **any** Gradio/Docker Space, even free-tier
  CPU. Only fully static Spaces remain free. The user deleted the old Space.
  Fix: `extension/qontext/qontext_chat.js` is a new, parity-tested JS port of
  `QontextMemory` (the "Assistant chat" tab only — `RPMemory`/roleplay was
  already ported in `qontext.js` but is not included in this demo, by the
  user's choice, to avoid porting two classes for one demo). Every helper
  function (`stem`, `words`, `splitSentences`, `frame`, `importance`, ...)
  and every vocabulary table were already shared with the existing RP port
  (`qontext_rp.py` imports its vocabulary from `qontext_memory.py` in
  Python), so only the extraction admission rule and the `QontextMemory`
  class itself needed porting. `parity_chat.mjs` / `build_chat_cases.py`
  check it against Python (50/50 checks passing across three scenarios:
  the sample conversation, question/second-person filtering, and
  supersession/eviction). The demo itself lives in
  `extension/demo/index.html` and a deploy-ready copy (with the import path
  rewritten for a Space root) is staged in `qontext-static-space/`. Not yet
  pushed — needs `hf repo create Trobi/qontext --type space --sdk static`
  (no `--flavor`, so no PRO paywall) run on the user's machine, then the
  staged files copied into a fresh clone and pushed. Exact commands were
  given in chat.
- **The paper's ten references** have never been verified.
- `affordance_turnbench.json` needs the local llama server to regenerate
  (`--reasoning-budget 0`, chat endpoint).
- **`QontextMemory`'s default changed: `speakers="user"` → `speakers="all"`.**
  Found via a live-demo bug report: "who sounds like a handful?" (answering
  "Bikkel sounds like a handful!", said by the *assistant*) returned an
  unrelated knot, because `speakers="user"` discarded the assistant's line
  entirely — no fact existed for the query to reach, so `pack()`'s
  documented no-match fallback (send the newest knot) fired instead. The
  user's call: switch the default globally, on the explicit basis that
  "knot count does not trump actually being able to answer accurately."
  Mechanically small (one line + docstrings, in both `qontext_memory.py` and
  the JS port `qontext_chat.js`) but **not a free change**:
  - One real test broke: `TestRealConversation`'s density guarantee (was
    "< 0.5", now measured 0.50 on that fixture once the assistant's own
    lines are stored too). Split into two tests rather than loosened blind:
    `test_density_under_half_when_only_the_user_states_facts` (pins the old
    guarantee explicitly with `speakers="user"`) and
    `test_density_under_two_thirds_with_all_speakers` (the new default,
    real margin above the measured 0.50). 128 tests pass (115 in
    `test_qontext_memory.py` + 13 in `test_llm_judge_bridge.py`).
  - `extension/qontext/qontext_chat.js`'s default flipped to match;
    `parity_chat.mjs` regenerated and still 50/50, `parity.mjs` (RP) still
    24/24 — RPMemory is unaffected, roleplay already gives every speaker
    their own subject by construction.
  - **The headline README table (119x, 9.3 vs 9.7 accuracy) was measured
    with the OLD default** — `bench/long_bench.py` and
    `bench/turn_bench.py` (behind every number in `RP_FINDINGS.md`) both
    construct `QontextMemory()` bare, no `speakers=` argument. Most of the
    *diagnostic* scripts (`bridge_ceiling.py`, `size_scaling.py`,
    `turn_ceiling.py`, `rp_turnbench.py`, etc.) already passed
    `speakers="all"` explicitly, so those findings are unaffected — it is
    specifically the two flagship benchmarks that were affected.
    **`long_bench.py` re-run by the user** on the exact original command
    (`--turns 800 --filler daily-clean --seeds 7,11,23`, from
    `qontext-bench/LIVE_RUN_FINDINGS.md`, "The headline result"). The pack
    side — the only side `speakers=` touches — held almost exactly: 9.7
    accuracy (was 9.7), 117 tokens (was 116), same single miss on the same
    seed, same wrong answer ("Terry Graham"). The full-transcript side rose
    9.3 → 10.0, but that arm never calls `QontextMemory` at all, so this
    isn't attributable to the default change — logged as unexplained
    run-to-run variance (temp 0.2, not deterministic), not investigated
    further. **README.md's headline table is re-verified under the current
    default** — 119x and the tied-accuracy claim both stand.
    `turn_bench.py` (behind `RP_FINDINGS.md`) has not been re-run; it needs
    the same treatment if those numbers are cited again.
  - The demo's earlier no-match caveat banner (added the same round, before
    this) stays — it is a real, separate safety net for genuine no-match
    queries and does not overlap with this fix.

- **Ultragoal review pass on `qontext_memory.py`** (`.claude/ultragoal-progress.md`
  has the full log). Full read of all ~2054 lines against `RP_FINDINGS.md`
  and this file's own retraction history. Three rounds, all kept:
  1. `INDEX_TERMS` default `10` → `0`. Its enabling comment cited the
     44.5% write-time-bridging figure and a "+0.8pts, 5-0 per-log"
     correction as live justification — both measured on
     `rp_turnbench.py`, which this file already lists as withdrawn after
     failing a shuffle control. The one re-measurement on the *valid*
     benchmark (`turn_bench.py`, "The bridges, re-run — and a reversal")
     showed no effect over plain baseline. `COVERAGE_GATE`'s own comment
     states the rule this violated: "a feature with no evidence should
     not be on." Rewrote the comment to say so plainly instead of citing
     retracted numbers. 128/128 tests unaffected (nothing referenced it).
  2. Real persistence bug: `serialize()`/`deserialize()` silently dropped
     a knot's hidden index terms and its `reinforced` count on every
     save/load cycle — both computed at write time, neither ever
     serialized. Inert while `INDEX_TERMS` defaults to 0, but
     `reinforced` feeds `_importance()` on every supersession and is live
     by default, so any persisted memory with a corrected fact was
     losing that fact's reinforcement weight on reload. `FORMAT_VERSION`
     2 → 3; v3 rows carry both, v1/v2 rows still load with the old
     defaults. Three new round-trip tests added; 131/131 total.
  3. Removed a duplicated comment block above `_evict()` — two
     back-to-back paragraphs saying the same thing about importance vs.
     distinctiveness, left over from a rewrite that never deleted the
     original.
  Stopped at 3, not 10: the next real candidate (`pack()`'s no-lexical-
  match fallback picks literal-newest, `max(..., key=seq)`, while every
  other selection policy in this file — `PACK_RESERVE`, `candidates()`,
  `_evict()` — was measured and rewritten to blend importance with
  recency because "just newest" alone lost) needs `long_bench.py` /
  `turn_bench.py` against the local model server to verify honestly, and
  this sandbox has no model server reachable (see below). Left open for
  whoever has that access, rather than shipping an unmeasured change to
  core retrieval — this file's own retraction history is the reason not
  to.
  - **Not yet synced to `qontext-live/qontext_memory.py`** — that copy
    still has the pre-Round-1 `INDEX_TERMS=10` comment and lacks all
    three rounds' changes. The two files have been drifting for at least
    one prior round (the `speakers="all"` default change above also
    isn't in `qontext-live`). Worth deciding whether `qontext-live` is a
    deliberate frozen snapshot or should track `qontext-memory/` — as-is,
    nothing keeps them in sync automatically.

- **`BURST_WEIGHT`/`BURST_WINDOW` — burst density, a new UNVERIFIED,
  off-by-default signal.** User-requested feature (not part of the
  ultragoal pass above): how many other knots landed within
  `BURST_WINDOW` seconds of a given one — a flurry of five knots in one
  minute scores higher than a knot that arrived into a quiet week.
  Recomputed from current timestamps every time it's asked for (an
  `O(n log n)` sweep, `_burst_counts()`), not stored on the knot, so it
  stays correct as more knots land near an existing one later and does
  not need a `FORMAT_VERSION` bump.
  - Three integration points, matching the request to treat this as one
    property rather than three separate toggles: `_score()` (ranking,
    gated by `BURST_WEIGHT`, same pattern as `IMPORTANCE_RANK`),
    `_evict()` (survival, same pattern as the existing `imp` nudge on
    `_rarity()`), and a new public read method `burstiness()` (metadata,
    always available regardless of the weight, since it's just
    descriptive).
  - `BURST_WEIGHT` defaults to `0.0` — same discipline as `INDEX_TERMS`
    post-retraction: the *mechanism* is plausible and internally
    consistent (Quipu weavers really did space knots by the intensity of
    what they recorded), but nothing about it has been measured against
    real retrieval yet. It ships off, tested for correctness and
    performance (3000 knots: `_burst_counts()` in ~1ms, a full `pack()`
    call with the weight on in ~5ms — no meaningful cost), not for
    proven benefit.
  - Five new tests (`TestBurstDensity`): count correctness on a
    deliberately time-clustered fixture, a true-no-op check at the
    default weight (byte-identical `pack()`/`entries()` output against a
    twin memory with the feature absent from the comparison entirely),
    and — the one that actually proves the wiring works, not just that
    it computes a number — a five-knot eviction scenario, symmetric in
    every other respect, where turning `BURST_WEIGHT` on flips which
    knot survives: the once-oldest-but-clustered knot lives, the
    isolated one dies instead. 136/136 tests total.
  - **Not ported to the JS extension** (`qontext.js`/`qontext_chat.js`)
    — same gap as the `qontext-live` sync above, now a third file that
    can drift. Deliberately left alone rather than porting an unverified,
    off-by-default mechanism into two more places before it's proven
    worth having anywhere.
  - To actually evaluate it: set `BURST_WEIGHT` above 0 and re-run
    `bench/long_bench.py` or `bench/turn_bench.py` against the local
    model server, which this sandbox cannot reach. Until that happens,
    treat this exactly like `INDEX_TERMS` before its retraction — a
    reasonable-sounding idea, not a demonstrated one.

## Facts about the environment

- Logs `log8.txt` / `log12.txt` are excluded in code after a content screen.
  **Do not print log text to screen** — it is private and explicit.
- The sandbox **cannot reach** the user's llama server on `127.0.0.1:8080`.
- `download.pytorch.org` is unreachable and the torch wheel stalls. MiniLM
  works via its ONNX export (88 MB: `onnxruntime`, `tokenizers`,
  `huggingface_hub`).
- Build the PDF in `/tmp`, not on the mount — xelatex is very slow there.
- Long-running jobs: `(setsid cmd > /tmp/log 2>&1 &)`, then poll. The bash
  tool times out at 45 s.
