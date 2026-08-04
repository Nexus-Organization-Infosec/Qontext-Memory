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
- **HF Space** still blocked by the abuse-handler flag; appeal not confirmed.
- **The paper's ten references** have never been verified.
- `affordance_turnbench.json` needs the local llama server to regenerate
  (`--reasoning-budget 0`, chat endpoint).

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
