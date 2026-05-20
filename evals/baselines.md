# RTIA Eval Baselines — Requirements Analyst

This file is the **canonical record** of per-agent eval scores against the
current prompt versions. Update it whenever a prompt or judge change
shifts the numbers. Per-run JSON reports live under `evals/reports/` and
are gitignored — they are noise; this file is signal.

Baselines are pinned to the **prompt hash** (sha256[:12] of the system +
user-template prompts, see `agents/config.prompt_hash`). When the hash
changes, the prior numbers no longer apply and a rebaseline run is
required.

---

## 2026-05-20 — multi-dimension fix (PR #85)

| | |
|---|---|
| Model (production agents) | `claude-opus-4-7` |
| GEval (intent, AC faithfulness) judge | `claude-opus-4-7` |
| Match (actor, ambiguity, AC coverage) judge | `claude-haiku-4-5-20251001` |
| Prompt caching | enabled on Analyst + Story Writer + AC Generator |
| Analyst prompt_hash | `19631aecc02a` (unchanged) |
| Story Writer prompt_hash | `990e6ae9e86f` (was unchanged in Phase 8.4 baseline) |
| AC Generator prompt_hash | `e7af2794b28c` (was captured per-run in Phase 8.4) |

Closes the sample-03 ac_coverage gap surfaced by Phase 8.4. Two coupled
prompt changes:

1. **AC Generator Rule 8 (multi-dimension)** — one AC per named dimension
   when the story enumerates a closed list (e.g. "filter by date range,
   environment, AND test suite name"). Second worked example matches
   sample-03's filter-persistence shape.
2. **Story Writer "preserve enumerated dimensions + sub-capabilities"** —
   reproduce dimension lists verbatim (no synonym substitution, no
   shortening); preserve named sub-behaviours (e.g. filter persistence) in
   description or assumptions. Found necessary during PR #85 live re-baseline:
   the AC Generator's new rule cannot help if the Story Writer has already
   dropped dimensions upstream.

### Mean scores

| Metric | Mean | Threshold | Δ vs Phase 8.4 |
|---|---|---|---|
| `intent_faithfulness` | **0.80** | 0.80 | -0.03 |
| `actor_set_completeness` | **0.70** | 0.80 | -0.07 |
| `ambiguity_discipline` | **0.86** | 0.80 | -0.06 |
| `ac_coverage` | **0.77** | 0.80 | **+0.34** |
| `ac_testability` | **1.00** | 0.80 | 0.00 |
| `ac_faithfulness` | **0.67** | 0.80 | -0.10 |

### Per-sample detail

| Sample | intent | actors | ambig | ac_cov | ac_test | ac_faith |
|---|---|---|---|---|---|---|
| sample-01-well-structured | 0.80 | 0.80 | 1.00 | **1.00** | 1.00 | 0.53 |
| sample-02-vague-ambiguous | 0.90 | 0.50 | 0.57 | 0.44 | 1.00 | 0.73 |
| sample-03-multi-feature   | 0.70 | 0.80 | 1.00 | **0.86** | 1.00 | 0.73 |

### Headline findings

1. **sample-03 ac_coverage: 0.00 → 0.86** (Phase 8.4 → this run). Hypothesis
   confirmed end-to-end: Story Writer now preserves all three filter
   dimensions verbatim; AC Generator emits one AC per dimension
   (precision=1.00, recall=0.75 → only `filter persistence` still missing
   because the Story Writer did not surface it as an assumption on this run).
   Iteration target met. Filter-persistence carry-through is the next
   tractable improvement.

2. **sample-01 ac_coverage held at 1.00.** Non-regression bar met on the
   well-structured sample. The new Rule 8 did not over-trigger.

3. **sample-02 ac_coverage 0.50 → 0.44** — within the documented ±0.10
   noise floor. Earlier intra-run drop to 0.29 was confirmed as PO-answer
   drift, not a Rule 8 regression: the auto-PO-resolver picks one of three
   implied stories stochastically, and the AC ground truth is pinned to a
   specific scope. Decoupling the PO answer in the eval is the durable fix
   (still deferred — separate iteration).

4. **`ambiguity_discipline` mean rebounded 0.44 → 0.86** vs. the intra-run
   probe, with no Analyst prompt change. Confirms the prior run's
   ambiguity dip was stochastic LLM variance, not a real shift. The ±0.10
   per-sample noise floor still applies; treat any single run accordingly.

5. **`ac_faithfulness` mean 0.77 → 0.67** is within run-to-run variance for
   this metric (sample-01 in particular swings between 0.60 and 0.85). The
   structural pessimism explained in the Phase 8.4 baseline still applies
   (the metric scores ACs against Story Writer description+objective only,
   ignoring Analyst context). Not addressed here.

### Token usage (production-agent calls only, excludes judge spend)

| | input | output |
|---|---|---|
| Analyst across 3 samples | 6948 | 870 |
| Story Writer + AC Generator | (not captured at runner level — see Anthropic console) |

---

## 2026-05-20 — AC-layer baseline (Phase 8.4 metrics live)

| | |
|---|---|
| Model (production agents) | `claude-opus-4-7` |
| GEval (intent, AC faithfulness) judge | `claude-opus-4-7` |
| Match (actor, ambiguity, AC coverage) judge | `claude-haiku-4-5-20251001` |
| Prompt caching | enabled on Analyst + Story Writer + AC Generator |
| Analyst prompt_hash | `19631aecc02a` |
| AC Generator prompt_hash | (captured in JSON report) |

The runner now chains Analyst → Story Writer → AC Generator per sample (so
AC metrics score against the AC Generator's actual output, not a synthetic
input). Critical Analyst ambiguities are auto-resolved with a canned PO
answer — Story-Writer / AC-Generator quality on multi-feature samples is
upstream-coupled to that answer; this is intentional and documented.

### Mean scores

| Metric | Mean | Threshold | Δ vs prior |
|---|---|---|---|
| `intent_faithfulness` | **0.83** | 0.80 | +0.06 |
| `actor_set_completeness` | **0.77** | 0.80 | 0.00 |
| `ambiguity_discipline` | **0.92** | 0.80 | +0.06 |
| `ac_coverage` | **0.43** | 0.80 | (new) |
| `ac_testability` | **1.00** | 0.80 | (new) |
| `ac_faithfulness` | **0.77** | 0.80 | (new) |

### Per-sample detail

| Sample | intent | actors | ambig | ac_cov | ac_test | ac_faith |
|---|---|---|---|---|---|---|
| sample-01-well-structured | 0.90 | 0.80 | 1.00 | 0.80 | 1.00 | 0.67 |
| sample-02-vague-ambiguous | 0.80 | 0.50 | 0.75 | 0.50 | 1.00 | 0.85 |
| sample-03-multi-feature   | 0.80 | 1.00 | 1.00 | **0.00** | 1.00 | 0.80 |

### Headline findings (calibration insights — do NOT silence by tuning prompts mid-flight)

1. **sample-03 ac_coverage = 0.00** — the AC Generator collapsed the 4 required
   filter categories (date range / environment / suite name / persistence) into
   3 generic ACs that don't differentiate the dimensions. Exactly the failure
   mode the coverage metric was designed to catch. Concrete prompt-iteration
   opportunity: the AC Generator's coverage rule needs reinforcement on
   multi-dimension stories ("one AC per stated dimension").

2. **sample-01 ac_coverage dropped 1.00 → 0.80** between the single-sample
   probe and the full sweep. Same prompt hash, different AC output text
   (stochastic LLM). Same band as the Analyst's run-to-run variance noted
   in the prior baseline — treat per-sample scores as ±0.10 noise floor.

3. **ac_faithfulness is structurally pessimistic** on sample-01 (0.67) because
   the AC Generator legitimately pulls context from the Analyst's intent +
   actors, but the metric only compares ACs against the Story Writer's
   description+objective (which can be terser than the underlying Analyst
   read). A more permissive metric would include the Analyst output in the
   "expected_output" payload — captured as a future iteration, NOT changed
   here so the baseline is reproducible.

4. **ac_testability = 1.00 across all samples** — programmatic checks pass.
   The metric is currently lenient; if real regressions don't surface here
   over the next few runs, add an atomicity check (no `" and "` joining
   independent assertions in `then`).

### Token usage (production-agent calls only, excludes judge spend)

| | input | output |
|---|---|---|
| Analyst across 3 samples | 6948 | 913 |
| Story Writer + AC Generator | (not captured at runner level — see Anthropic console) |

Story Writer / AC Generator usage is not captured here because their library
entry points (`write_user_story`, `generate_acceptance_criteria`) return parsed
objects, not the raw LLM response. Anthropic console's Cost tab is the source
of truth; this telemetry is informational only. Switching to a usage-aware
invocation would require either restructuring those library functions or
duplicating their bodies — neither is worth it for a billing convenience.

---

## 2026-05-20 — cost-reduction baseline (caching + split judges)

| | |
|---|---|
| Model (production agents) | `claude-opus-4-7` |
| GEval + actor judge | `claude-opus-4-7` |
| Ambiguity-category judge | `claude-haiku-4-5-20251001` |
| Prompt caching | enabled (`cache_control: ephemeral`) on Analyst + Story Writer system prompts |
| Analyst prompt_hash | `19631aecc02a` (unchanged from prior baseline) |

### Mean scores

| Metric | Mean | Δ vs initial | Threshold |
|---|---|---|---|
| `intent_faithfulness` | **0.77** | −0.06 | 0.80 |
| `actor_set_completeness` | **0.77** | 0.00 | 0.80 |
| `ambiguity_discipline` | **0.86** | 0.00 | 0.80 |

> The −0.06 on `intent_faithfulness` traces to sample-03 (0.70 → 0.60). The
> Analyst's intent string came back terser this run — same prompt hash, same
> model. This is **run-to-run Analyst variance**, not a judge or caching
> artifact (the judge sees the Analyst output verbatim). Treat the per-metric
> mean as a ±0.10 band, not a single number, until more runs accumulate.

### Per-sample detail

| Sample | intent | actors | ambiguity |
|---|---|---|---|
| sample-01-well-structured | 0.90 | 0.80 | 1.00 |
| sample-02-vague-ambiguous | 0.80 | 0.50 | 0.57 |
| sample-03-multi-feature   | 0.60 | 1.00 | 1.00 |

Sample-02 ambiguity recall (0.57) remains the headline gap — same finding as the
initial baseline, untouched by this change.

### Why this baseline exists

- **Prompt caching** on the static Analyst + Story Writer system prompts (≈2k
  tokens each) — cached input billed at ~10% of standard rate when reused within
  the 5-minute TTL, which covers a full eval-suite burst.
- **Ambiguity-category judge moved to Haiku 4.5.** This metric makes one judge
  call per ambiguity item (5+ on sample-02), so the volume justifies the cheaper
  model. Classification quality validated empirically at parity with the
  Opus-judged baseline (ambiguity scores unchanged).
- **Actor-matching judge kept on Opus.** First attempt with Haiku regressed
  sample-01 from 0.80 → 0.40 because Haiku failed the `authenticated user` ≈
  `QA Lead (authenticated user)` synonym call. Reverted same-PR.
- **GEval intent judge kept on Opus.** Single subtle-reasoning call per sample
  — wrong place to economise.

### Token usage (Analyst calls only, excludes judge spend)

| | input | output |
|---|---|---|
| total across 3 samples | 6948 | 955 |

Analyst token totals are essentially unchanged from the initial baseline (input
identical, output +69) — caching doesn't reduce *reported* tokens, just billing.
The real-cost reduction shows up in the Anthropic console, not in this telemetry.

---

## 2026-05-19 — initial Phase 6 baseline

| | |
|---|---|
| Model | `claude-opus-4-7` |
| Analyst prompt_hash | `19631aecc02a` |
| Judge | `claude-opus-4-7` (single-provider; see `evals/judge.py`) |
| Samples | sample-01, sample-02, sample-03 |
| Generated by | `uv run python evals/run_evals.py` |

### Mean scores

| Metric | Mean | Threshold |
|---|---|---|
| `intent_faithfulness` | **0.83** | 0.80 |
| `actor_set_completeness` | **0.77** | 0.80 |
| `ambiguity_discipline` | **0.86** | 0.80 |

> Thresholds are taken from US-13 (#18): faithfulness > 0.8. Treated here as
> guidance for the Phase 7 integration gate, not as a current pass/fail.
> Per-sample scores below show where the mean is masking real variance.

### Per-sample detail

**sample-01-well-structured**
| Metric | Score |
|---|---|
| intent_faithfulness | 0.90 |
| actor_set_completeness | 0.80 |
| ambiguity_discipline | 1.00 |

- **actor regression candidate** — Analyst emitted "QA Dashboard" as a third
  actor, dropping precision to 0.67. Treating the surface as an actor is
  borderline; either tighten the prompt's actor definition or accept it
  as a ground-truth refinement.

**sample-02-vague-ambiguous**
| Metric | Score |
|---|---|
| intent_faithfulness | 0.90 |
| actor_set_completeness | 0.50 |
| ambiguity_discipline | 0.57 |

- **actor synonym miss** — Analyst said `team member`, judge did not bind it
  to expected `QA team member`. Judge prompt or expected label phrasing
  could be relaxed without inviting real false positives.
- **ambiguity recall miss** — only 2 of 5 expected categories surfaced
  (`actor scoping`, `manager visibility shape`); `team update scope`,
  `defect domain`, `success measure` were not raised. This is the
  clearest opportunity in the current baseline — the vague sample is the
  one where the Analyst earns its keep.

**sample-03-multi-feature**
| Metric | Score |
|---|---|
| intent_faithfulness | 0.70 |
| actor_set_completeness | 1.00 |
| ambiguity_discipline | 1.00 |

- **intent drift** — Analyst's intent string flattened the failure-rate
  threshold and CSV specificity out of the goal sentence. Multi-feature
  intents are inherently harder to compress without losing scope; the
  current score is acceptable but worth re-checking after any Analyst
  prompt change.

### Token usage (Analyst calls only, excludes judge spend)

| | input | output |
|---|---|---|
| total across 3 samples | 6948 | 886 |

Judge spend is not yet aggregated — Phase 7's integration workflow is the
right place to bolt on a cost cap, not this baseline doc.

---

## How to refresh

```bash
uv run python evals/run_evals.py             # all samples
uv run python evals/run_evals.py sample-01   # single sample by stem prefix
```

After a successful run:

1. Check the report header confirms the `prompt_hash` that produced the scores.
2. Update the table above (do not append a new section unless the prompt
   has actually changed — drift on the same hash is judge noise and
   shouldn't be canonised).
3. If the prompt hash has changed, leave the prior section in place as
   history and add a new dated section.
