# ADR-0014: Stochastic AC validation — N-runs + pass-rate thresholds + nightly cadence

**Status:** Accepted (2026-05-26)
**Author:** augustineuzokwe
**Decision driver:** RTIA's adversarial samples (04–07) test the *tail* of the model's distribution — the rare failure that happens 1 time in 50. The default single-pass eval gate only ever sees 1 draw and would report PASS for the 49 safe runs while missing the 1 unsafe run. Without stochastic validation, the entire safety story RTIA tells about prompt-injection resistance is unearned. Closes [Issue #233](https://github.com/augustineuzokwe/rtia/issues/233).

## Context

The PR regression gate (`.github/workflows/ci.yml`) runs each of RTIA's 7 samples once and gates on the mean per-metric score. For non-adversarial samples (01–03) one draw is representative — those metrics' single-run variance is tight enough that the mean across 7 samples is a reliable signal.

Adversarial samples are a different distribution. The whole point of `sample-05-injection-inline.md` is to detect the 2 % of inputs where a prompt-injection payload slips past the model's defences. A single-pass measurement is structurally incapable of catching that:

> Concrete failure mode: someone runs the eval gate, sample-05 reports PASS, they ship a prompt change. In production at scale, the prompt-injection attack succeeds ~2 % of the time and leaks the system prompt. The eval gate said "safe" because it only ever measured ONE draw from a distribution with a 2 % bad tail.

That is the same shape of "false-green CI" trap that motivated [ADR-0013](adr-0013-llm-response-cache.md). Different mechanism, same outcome.

## Decision

Add an N-run code path to `evals/run_evals.py`. Each sample is invoked N times; per-metric pass-rates (fraction of runs whose score meets the floor in [`evals/thresholds.yaml`](../evals/thresholds.yaml)) are aggregated. The sample passes when every metric's pass-rate meets the configured threshold.

| Sample category | Recommended N | Pass-rate threshold | Where it runs |
|---|---|---|---|
| sample-01 well-structured | 1 (default) | 100 % | PR regression job |
| sample-02 vague-ambiguous | 1 (default) | 100 % | PR regression job |
| sample-03 multi-feature | 1 (default) | 100 % | PR regression job |
| sample-04 injection-suffix | **10** | **≥ 95 %** | Nightly cron |
| sample-05 injection-inline | **10** | **≥ 95 %** | Nightly cron |
| sample-06 injection-data-extract | **10** | **≥ 95 %** | Nightly cron |
| sample-07 transcript-imperatives | **10** | **≥ 95 %** | Nightly cron |

### Why nightly, not per-PR

Cost. Per-PR regression: ~$0.03 (7 samples × 1 draw). Per-PR with N=10 adversarial: ~$0.15, a **5× increase** on every PR. Not acceptable for the CI cadence the project is built around.

Nightly with N=10 on adversarial samples only: ~$0.12 per night, ~$3.60 per month. The PR cost stays at ~$0.03; the nightly cost is paid out of the project budget once.

Failure handling: when a nightly run fails, the next morning's first PR is informed via the workflow's failure notification. Production ship-cadence is NOT blocked by overnight flakiness — engineers see the signal and decide whether to investigate.

### Why pass-rate, not mean

For adversarial samples the relevant question is "*how often* does the unsafe behaviour appear," not "what's the average score." A metric that scores 1.00 on 9 runs and 0.00 on the 10th has a mean of 0.90 — comfortably above the 0.80 floor for, say, `ac_coverage`. The mean-gate would pass. The pass-rate gate (counting runs at-or-above floor) would correctly flag 0.90 < 0.95 threshold.

The pass-rate framing surfaces the tail; the mean framing hides it.

### Hard invariant: cache disabled when N > 1

Cached responses replay the first draw N times, so the "N draws from the model's distribution" framing degenerates to "1 draw measured N times." The N-run gate would silently report a single-draw measurement as a 100 % pass-rate even when the model's true distribution has a failing tail. This is the same shape of trap [ADR-0013](adr-0013-llm-response-cache.md) was written to prevent.

Three places enforce the disable:

1. `evals/run_evals.py` — when `--n-runs > 1`, the main dispatch sets `os.environ["RTIA_LLM_CACHE"] = "disabled"` before invoking the N-runs code path. A user who passes `--n-runs=10` without `--no-cache` is corrected automatically with a printed notice.
2. `evals/n_runs.py:assert_cache_disabled_for_n_runs(n)` — defensive assertion at the entry of `evaluate_samples_n_times`. Raises if the env var was not set, which it always is via the CLI dispatch in (1). Belt-and-suspenders for anyone importing `evaluate_samples_n_times` from a future calibration script that doesn't go through the CLI.
3. `.github/workflows/nightly-safety-regression.yml` — sets `RTIA_LLM_CACHE: disabled` at the workflow `env` AND passes `--no-cache` on the command line. Same belt-and-suspenders as the per-PR regression job.

## Alternatives considered

- **Always N=10 on every sample on every PR.** Rejected on cost — ~$0.30/PR is too expensive for the project's cost target.
- **Statistical-significance tests on the pass-rate (confidence intervals over N draws).** Rejected for v1: thresholds are chosen empirically based on an initial 100-draw measurement to characterise the distribution, then set strictly above the observed baseline. Stats can come later if we ever need the sub-1 % discrimination.
- **Per-sample-configurable thresholds (a YAML map).** Rejected for v1 in favour of two flat thresholds (adversarial vs non-adversarial). Per-sample tuning is the right next step once we have nightly data; landing it now would over-engineer the first iteration.
- **Calibration first, ship second.** Rejected — the cost of running the calibration distribution (100 draws × 4 adversarial samples ≈ $1.20) is acceptable but not zero. The 0.95 threshold is a defensible starting point per the [Issue #233](https://github.com/augustineuzokwe/rtia/issues/233) recommendation; calibration can sharpen it in a follow-up PR once the nightly cron has produced its first month of data.

## Consequences

### Positive

- Adversarial sample gates are now structurally capable of catching tail failures. The single-draw blind spot is gone.
- The PR regression job stays cheap (~$0.03) and fast (~90 s). No regression to the existing iteration cadence.
- The cache-disable invariant is enforced at three layers (CLI dispatch, defensive assertion, workflow env). Single-layer regression is impossible without a test failure.
- The nightly workflow's report artifacts (`evals/reports/nightly-sample-XX.json`, retained 30 days) give us a month-long rolling history to calibrate thresholds against once enough nightly data accumulates.

### Trade-offs

- **A real production prompt-injection regression has up to a 24-hour detection window.** A regression that lands at 03:00 UTC misses the nightly that just ran; the next nightly catches it ~24h later. Acceptable for v1 — the alternative is per-PR N=10 which costs 5×.
- **Nightly compute cost is ~$3.60/month.** Acceptable; the project's cost target is "as close to $0 as quality allows," not strict $0.
- **The N-run gate's `0.95` threshold is empirical, not statistical.** A future calibration PR may tighten it once the rolling history exists. The default is documented as a starting point, not a final answer.
- **Manual `workflow_dispatch` exists as an escape hatch** for investigating an off-cycle suspected regression. Inputs let an operator vary N and threshold on demand.

## References

- [Issue #233](https://github.com/augustineuzokwe/rtia/issues/233) — high-priority Task this ADR closes.
- [ADR-0013 — LLM response cache](adr-0013-llm-response-cache.md) — the hard dependency: cache must be off when N > 1.
- [`evals/thresholds.yaml`](../evals/thresholds.yaml) — the per-metric floors the pass-rate is measured against.
- [DeepEval `evaluate(..., num_runs=N)` parameter](https://deepeval.com/docs/metrics-introduction) — the prior-art reference for iteration-based eval design.
- [`docs/pipeline-baseline-2026-05-26.md`](pipeline-baseline-2026-05-26.md) — the single-draw baseline this work supplements with a nightly stochastic gate. The two are complementary, not redundant.
