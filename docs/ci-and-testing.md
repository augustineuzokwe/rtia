# When tests fire - RTIA's testing pyramid

> ⚠ **Partially stale (post #332)**: the per-PR "Gemini eval gate" referenced in
> the diagrams + tables below was deleted from `.github/workflows/ci.yml` and
> is being moved to a nightly cron (`nightly-eval.yml`, follow-up to #332).
> The table at "Triggers at a glance" is up to date. The ASCII timelines and
> the "Mental shortcut" / pyramid sections will be rewritten alongside PR 2.

This doc maps every validation surface in RTIA - unit tests, eval gates,
nightly crons, manual scripts - to its **trigger**, **cost**, and
**purpose**. Aimed at the contributor wondering *"will my change get
caught before it merges, or after?"*

For *what each test file covers*, see [`tests/README.md`](../tests/README.md).
For the *why* behind specific design choices, see the relevant ADR
([0013 - cache](adr-0013-llm-response-cache.md), [0014 - N-runs](adr-0014-stochastic-ac-validation.md), [0008 - observability](adr-0008-pii-langsmith.md)).

## Triggers at a glance

| Surface | What runs | When it fires | Cost |
|---|---|---|---|
| `uv run pytest -q` (526 tests) | Every PR - CI **quality** job, [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | Always, on every push to a PR or `main` | $0 (mocked) |
| `pre-commit run --all-files` (ruff, format, detect-secrets) | Every commit (local hook) + every PR (CI quality job) | Always, on every commit + every PR push | $0 |
| `evals/run_evals.py` (Gemini eval gate, 7 samples) | Nightly `nightly-eval.yml` workflow (follow-up to #332) | Cron, plus manual `workflow_dispatch`. **No longer on every PR** — moved off the PR critical path because ~30 sequential live Gemini calls per PR made the gate fail on backend weather (502/503/504) rather than real regressions. See `.github/workflows/ci.yml` header for the full rationale. | ~$0.03 per fire |
| `evals/check_thresholds.py` (per-metric floor gate) | Right after the nightly `run_evals.py` succeeds | Nightly | $0 (post-processing only) |
| `evals/check_budgets.py` (token + latency gate) | Right after `check_thresholds.py` (nightly) | Nightly | $0 |
| `TestNightlyWorkflowContract` (in [`test_n_runs.py`](../tests/test_n_runs.py)) | pytest (already in the 568 count) | Every PR - locks the workflow YAML against silent regression. (Sibling `test_ci_cache_disable.py` was removed in #332 when the PR-tier eval was deleted; replacement tests against `nightly-eval.yml` will land with PR 2.) | $0 |
| Nightly safety regression (N=10 on adversarial samples 04–07) | [`.github/workflows/nightly-safety-regression.yml`](../.github/workflows/nightly-safety-regression.yml) | Cron `0 2 * * *` (02:00 UTC daily), plus manual `workflow_dispatch` from the Actions tab | ~$0.12/night, ~$3.60/month |
| `scripts/run_pipeline_demo.py` | **Manual** | You decide. Not CI-triggered. | ~$0.005 per deep run; cache may zero it |
| `scripts/run_api.py` | **Manual** | You decide. Long-lived process serving the UI + API. | ~$0.005 per pipeline run via UI |
| `scripts/run_integration_smoke.py` | **Manual** | You decide. Defaults to `--no-cache` because its purpose is live verification, not replay. | ~$0.03 per full 7-sample sweep |

## Visual - a PR's trigger timeline

```
You push a commit to a PR
   │
   ├─ pre-commit hook (local)           ← always, instant, $0
   │
   └─ CI workflow fires
        │
        ├─ quality job                  ← ALWAYS
        │    │
        │    ├─ pre-commit (ruff/secrets/format)
        │    └─ pytest -q (526 tests, includes CI-contract assertions)
        │
        └─ regression job               ← ONLY if agents/ prompts/ evals/ changed
             │
             ├─ run_evals.py (Gemini, 7 samples)         ~$0.03
             ├─ check_thresholds.py (per-metric floor gate)
             └─ check_budgets.py (cost + latency gate)
```

Path-filter precision matters: a docs-only PR triggers the quality job
(~30 s, $0) but skips the regression job (~6–11 min, $0.03). A PR
touching only `tests/` skips the regression job too - pytest runs the
updated tests, but the eval suite doesn't need to.

## Visual - a 24-hour timeline (no human action)

```
00:00 UTC  ──┐
             │
02:00 UTC  ──┼──→  nightly-safety-regression.yml
             │      ├─ N=10 × sample-04..07 (adversarial)
             │      ├─ cache OFF (env var + --no-cache, belt-and-suspenders)
             │      ├─ gates on per-metric pass-rate ≥ 95 %
             │      └─ ~$0.12 of Gemini judge calls
             │
06:00 UTC  ──┤
   …         │
             │
23:59 UTC  ──┘
```

A regression that lands at 03:00 UTC misses that night's run and is
caught the *next* night at ~24h. ADR-0014 documents the trade-off: per-PR
N=10 would 5× the PR cost; nightly N=10 keeps PRs cheap at the cost of a
24h detection window for adversarial-tail regressions.

## What's NOT automated

- **`run_api.py`** + **`run_pipeline_demo.py`** + **`run_integration_smoke.py`** - pure on-demand. CI never invokes them. They exist for **you** to validate things CI can't: interactive UX, exploratory runs, ad-hoc smoke checks.
- **Adversarial samples on PRs** - get only N=1. N=10 happens nightly. Adversarial regressions have up to 24h detection window.
- **Live LLM behaviour drift between Gemini versions** - would surface in the nightly first, the next PR's regression second.
- **The full-local Ollama path** - code-tested via unit tests, but the runtime deepeval ↔ ChatOllama wire is not exercised in CI (no Ollama on CI runners). Verified manually by the operator when they flip `RTIA_LLM_PROVIDER=ollama` + `RTIA_OLLAMA_JUDGE=1`.

## Mental shortcut

| If you… | …this runs |
|---|---|
| Push any commit | pre-commit + 526 pytest |
| Push a PR touching `agents/`, `prompts/`, or `evals/` | + Gemini eval gate (~$0.03) |
| Push a PR touching only docs or tests | quality job only ($0) |
| Wait until 02:00 UTC | adversarial N=10 (~$0.12) |
| Want to see the UI behave with real input | `scripts/run_api.py` (you) |
| Want a one-shot pipeline run | `scripts/run_pipeline_demo.py` (you) |
| Want to confirm nothing broke structurally end-to-end | `scripts/run_integration_smoke.py` (you) |
| Want to re-baseline the eval suite intentionally | `uv run python evals/run_evals.py --no-cache` (you) |

## Why this shape - the testing pyramid

```
                         ▲
                        ╱ ╲          $0.12/night
                       ╱   ╲         nightly N=10 adversarial probe
                      ╱     ╲        (stochastic, slow, thorough)
                     ╱───────╲
                    ╱         ╲      $0.03 per PR
                   ╱  Gemini   ╲     eval gate, 7 samples, N=1
                  ╱  eval gate  ╲    (measured, gated, conditional on path filter)
                 ╱───────────────╲
                ╱                 ╲  $0 per PR
               ╱   pytest 526      ╲ unit + contract + CI-YAML-lock
              ╱   + pre-commit      ╲(fast, always, deterministic)
             ╱───────────────────────╲
```

- **Bottom (fast/free):** every commit, every PR. Mocked LLM, contract assertions, lint. Catches structural breakage in seconds.
- **Middle (measured/cheap):** PRs that touch the AI surface. Real LLM, real metrics, real budget gates. Catches quality regressions before merge.
- **Top (nightly/thorough):** stochastic adversarial validation. Catches the 1-in-50 tail failures that single-pass measurement cannot.

The three `scripts/run_*.py` entry points are the **manual layer above all
of it** - the spots where your judgement is the gate, not a CI check.

## Cross-references

- [tests/README.md](../tests/README.md) - what each of the 40 test files actually covers
- [docs/adr-0013-llm-response-cache.md](adr-0013-llm-response-cache.md) - why the regression job disables the cache (belt-and-suspenders)
- [docs/adr-0014-stochastic-ac-validation.md](adr-0014-stochastic-ac-validation.md) - why nightly cadence not per-PR for N=10
- [docs/USAGE.md](USAGE.md) - daily-driver guide, including §10 on full-local mode
