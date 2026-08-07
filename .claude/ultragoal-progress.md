# Ultragoal progress
Target: qontext_memory.py
Requirement: complete review pass cross-referenced against RP_FINDINGS.md,
HANDOFF.md, qontext_rp.py, and this session's live findings.
MET — full read of qontext_memory.py (all 2054 lines), cross-referenced
against RP_FINDINGS.md and HANDOFF.md's retraction history.

## Key finding driving the plan
`INDEX_TERMS = 10` (enabled by default) is justified in the code's own
comment block by the "44.5% write-time bridging" oracle figure and a
"+0.8pts, 5-0 per-log" correction — both measured on `rp_turnbench.py`,
which HANDOFF.md explicitly lists as retracted ("Withdrawn: ... 44.5%
write-time bridging, index terms, ...") after failing a shuffle control.
The one re-measurement on the *valid* benchmark (RP_FINDINGS.md, "The
bridges, re-run — and a reversal", turn_bench.py table) shows "write-time
index terms" scoring identical to plain baseline (6.0/18 real, 6/42
turn-shaped, both exactly matching baseline with the mechanism off) --
i.e. no measured effect on the trusted benchmark. The file's own
COVERAGE_GATE comment states the applicable principle directly: "a
feature with no evidence should not be on" -- but that discipline was
never applied to INDEX_TERMS after its evidence was retracted.

## Rounds
(starting)

1. [x] INDEX_TERMS default 10 -> 0; rewrote its comment to state the
   retraction honestly instead of citing withdrawn numbers as live
   justification, mirroring COVERAGE_GATE's existing retraction-comment
   style in the same file. Measured: 128/128 tests pass unchanged
   (no test depended on it -- grep confirmed zero references before
   touching it). KEPT. Correctness/documentation-honesty fix, not a
   behavior regression: the file was shipping a default whose only
   evidence had been formally withdrawn, and whose one re-measurement
   on the valid benchmark (turn_bench.py) showed no effect.

2. [x] serialize()/deserialize() silently dropped `idx` (hidden index
   terms) and `reinforced` (supersession-inheritance count) on every
   save/load cycle -- both computed at write time, neither persisted.
   Bumped FORMAT_VERSION 2 -> 3; v3 rows carry both as a 5th/6th element;
   v1/v2 rows still load fine with the old empty/0 defaults (kept
   backward compatible, not a breaking change). Also gave v1-loaded
   knots an explicit `idx: frozenset()` for consistency (was previously
   an implicit `.get()` default only). Measured: added 3 new tests
   (reinforced survives round-trip with a real value > 0; idx survives
   round-trip with INDEX_TERMS temporarily re-enabled; old v2 rows
   without idx/reinforced still load with correct defaults) -- full
   suite now 131/131 passing (was 128/128). KEPT. Real correctness bug:
   inert today only because INDEX_TERMS defaults to 0 as of Round 1, but
   `reinforced` is live by default (feeds `_importance()` on every
   supersession) -- any persisted memory with a corrected fact was
   silently losing that fact's reinforcement weight on every reload,
   before this fix.

3. [x] Removed a duplicated comment block above `_evict()`'s sort key --
   two paragraphs explaining the same "importance nudges, doesn't
   outrank, distinctiveness" design decision back to back, near-identical
   wording, leftover from a comment rewrite that never deleted the
   original. Kept the more specific of the two (the one with the real
   "Docs are in Notion" example). Measured: 131/131 tests still pass
   (comment-only change, no behavior touched). KEPT. Readability fix.

## Stopped here -- 3 rounds, not 10
Looked for further rounds and found real candidates, but every one left
needs the local model server (`bench/long_bench.py` / `turn_bench.py`
call `http://127.0.0.1:8080`) to measure honestly, and this sandbox has
no model server running (checked: gguf files and a llama.cpp binary are
present in the mounted folder, but the binary is a Windows build, not
runnable here). The strongest candidate found this way: `pack()`'s
no-lexical-match fallback picks `max(self._knots, key=seq)` -- literally
newest, ignoring importance -- while every other selection policy in this
same file (`PACK_RESERVE`'s reserved slice, `candidates()`, `_evict()`)
was measured and rewritten to blend importance with recency/rarity
because "just newest" or "just importance" alone lost to the blend. That
inconsistency is real, but changing it without a live-model benchmark
would be exactly the "looks better, unverified" mistake this project's
own retraction history (INDEX_TERMS, rp_turnbench.py) warns against.
Per the skill's own instruction not to invent churn, and not to fake
verification: stopping rather than shipping an unmeasured change to core
retrieval. Left for the user to try (with `long_bench.py`) if they want
to chase it further.
