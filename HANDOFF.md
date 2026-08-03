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
| + embeddings K=30 @1500 | — | **68%**, control 5.4× |

Generalises: +8 on A, +8 on B. Chat suites untouched.
The affordance web is cleanly refuted (fails the control even after being
rebuilt over this benchmark's own vocabulary).
**Contextual MiniLM bought one item over static.** Use the static model.

---

## The next problem, stated precisely

At K=30 / budget 1500 the held-out score is 68%, but the pack goes 408 → 1448
chars and the control drops 19.2× → 5.4× (fails outright at budget 3000). So:

> The residual is **budget-bound, not reach-bound.** The correct knot for the
> hard categories sits at median rank ~24 of 172 — reachable, just outside a
> six-slot window.

**The work is precision, not a new semantic mechanism.** Move the rank-24 knot
to rank 5 and you buy the same items at a quarter of the cost without
degrading the control. Not yet tried: reranking the bridge's top-30 by
something cheap, query-side term weighting, choosing K adaptively from the
gap-kind profile.

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
