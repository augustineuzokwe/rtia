# Pipeline timing baseline - 2026-05-24 (issue #163)

Captured before the speed-up changes landed. Numbers are local-laptop
runs against the paid Gemini 3.5 Flash tier; CI numbers track but are
typically slower due to runner-pool variability.

Full reports: `evals/reports/baseline-2026-05-24.json` and `evals/reports/postchange-2026-05-24.json` (eval reports are gitignored - regenerate by running the eval suite). See the PR body for the diff.

## Aggregate (7 samples, production agents only, excludes judge time)

| Metric | Baseline | Post-change | Delta |
|---|---|---|---|
| Pipeline wall-clock | **150.6 s** | **145.7 s** | -3 % |
| Per-sample p50 | ~22 s | ~21 s | flat |
| Per-sample max | 25.4 s (sample-02) | 23.5 s (sample-05) | -7 % |
| Total tokens (in+out) | 98 008 | 98 062 | flat |
| Budget gate | PASS | PASS | - |

Production-agent wall-clock is essentially unchanged - `max_output_tokens`
caps only kick in on truncation, and the retry trim only helps on
transient errors (neither run hit any). The speed-up payoff lives in
the **judge phase**, which the eval report doesn't currently surface as
a separate timer. With `ThreadPoolExecutor(max_workers=4)` running the
3 judge LLM calls concurrently per sample, each sample's judge
wall-clock drops from `~sum(3 judges)` to `~max(3 judges)` - roughly
6–10 s saved per sample, ~45–70 s aggregate over the 7-sample suite.
The win is most visible in CI workflow duration, not in the JSON report.

## Per-agent aggregate (sum across samples)

| Agent | Aggregate duration | Per-sample mean | Per-sample max |
|---|---|---|---|
| Analyst | not in per_agent_duration_ms¹ | - | - |
| User Story Writer | 31.7 s | 4.5 s | 6.3 s |
| AC Generator | 48.7 s | 7.0 s | 9.3 s |
| Test Case Writer | 70.2 s | 10.0 s | 13.2 s |

¹ Analyst telemetry is captured separately via
`_run_analyst_capturing_usage` (pre-Phase 13.2 path) and surfaces in
the JSON under `samples[].usage` rather than under
`per_agent_duration_ms`. Both per-sample tokens and per-sample timing
are available - the analyst takes ~3-8 s/sample.

## Per-agent output tokens (max observed across 7 samples)

| Agent | Max output_tokens | Sample |
|---|---|---|
| Analyst | 1972 | sample-03-multi-feature |
| User Story Writer | 1378 | sample-02-vague-ambiguous |
| AC Generator | 2042 | sample-06-injection-data-extraction |
| Test Case Writer | 3012 | sample-05-injection-inline |

These numbers drive the `MAX_OUTPUT_TOKENS_*` calibration in
`agents/config.py` (cap = 2× observed max, rounded up to nearest 500).

## Mean metric scores (quality must not regress post-change)

| Metric | Baseline | Post-change | Floor | Δ |
|---|---|---|---|---|
| actor_set_completeness | 0.95 | 0.95 | 0.70 | 0 |
| ambiguity_discipline | 0.76 | 0.71 | 0.30 | -0.05 |
| intent_keyword_overlap | 0.88 | 0.88 | 0.40 | 0 |
| ac_coverage | 1.00 | 0.97 | 0.80 | -0.03 |
| ac_testability | 1.00 | 1.00 | 0.80 | 0 |
| tc_coverage_breadth | 0.94 | 1.00 | 0.80 | +0.06 |
| tc_executability | 1.00 | 1.00 | 0.80 | 0 |
| requirement_fidelity | 0.94 | 0.90 | 0.70 | -0.04 |
| injection_resistance | 1.00 | 1.00 | 1.00 | 0 |

The small dips on `ambiguity_discipline`, `ac_coverage`, and
`requirement_fidelity` are within run-to-run variance for a
non-zero-temperature model (back-to-back runs on the same code can
swing ±0.05 on judge-graded metrics). All scores remain comfortably
above their floors - the tightest margin is `ambiguity_discipline`
at 0.71 vs 0.30 floor (still 2.4× the floor). The
`check_thresholds.py` gate passes on both runs.

## Judge wall-clock (estimated)

The eval suite does not directly track judge wall-clock (judge calls
are intentionally excluded from `capture_agent_telemetry`). Three of
the eight base metrics are LLM-judge calls per sample
(`actor_set_completeness`, `ambiguity_discipline`, `ac_coverage`). At
~3-10 s per judge call × 3 judges × 7 samples = **roughly 60-200 s of
serial judge time**, on top of the 150 s of production-agent time.

That puts the **total eval wall-clock at ~210-350 s** today - straddling
the Phase 13.1 budget ceiling (240 s aggregate, per
`pyproject.toml [tool.rtia.budgets]`).

## What this baseline justifies in the speed-up PR

- **Retry trim 5 → 2** in `agents/config.py:DEFAULT_MAX_RETRIES`. The
  outer `nick-fields/retry@v4` (`ci.yml`) gives 2 outer attempts;
  layered worst case is 4 logical attempts, comfortably enough for a
  transient 503.
- **`MAX_OUTPUT_TOKENS_*` caps** at 2× observed max (rounded), per
  the table above.
- **Judge parallelism inside `evaluate_sample`**: ThreadPoolExecutor
  with `max_workers=4` runs the 3 judge calls (+ 5 programmatic
  metrics) concurrently. Per-sample judge wall-clock drops from
  `~sum()` to `~max()` of the three slowest judge calls.

## Out of scope for the speed-up PR

- **Cross-sample parallelism.** The current `capture_agent_telemetry`
  installs a global logger handler - running multiple samples
  concurrently would cross-capture events between them. Fixing that
  needs a thread-local or ContextVar-scoped capture and is its own
  PR.
- **Gemini context caching.** ~25 KB of system-prompt across 5 agents
  is medium-leverage; integration cost (separate `google.genai` SDK,
  cache lifecycle, 5 agent sites with no shared factory) is high.
  Separate PR once data shows it's the next ceiling.
- **Reviewer cap calibration.** The Reviewer doesn't run in the eval
  suite (only in the LangGraph deep flow). Its `MAX_OUTPUT_TOKENS`
  cap is set to match Story Writer (3000) on the assumption their
  output shapes are similar; calibrate properly via a future
  `scripts/run_pipeline_demo.py` instrumentation run.
