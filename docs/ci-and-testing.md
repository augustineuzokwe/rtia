# When tests fire - RTIA's testing pyramid

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
| `uv run pytest -q` (572 tests) | Every PR - CI **quality** job, [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | Always, on every push to a PR or `main` | $0 (mocked) |
| `pre-commit run --all-files` (ruff, format, detect-secrets) | Every commit (local hook) + every PR (CI quality job) | Always, on every commit + every PR push | $0 |
| `evals/run_evals.py` (Gemini eval gate, 7 samples) | **DISABLED on CI (#348, re-confirmed after the brief #340 re-enable).** The `regression` job is preserved in `.github/workflows/ci.yml` with `if: false`. Live eval is a manual/local step. See "Why the live eval gate is off" below. | $0 on CI; ~$0.03 per local run |
| `evals/check_thresholds.py` (per-metric floor gate) | Inactive on CI while regression is disabled; runs locally as part of `evals/run_evals.py` | — | $0 |
| `evals/check_budgets.py` (token + latency gate) | Inactive on CI while regression is disabled; runs locally as part of `evals/run_evals.py` | — | $0 |
| `test_ci_cache_disable.py` + `TestNightlyWorkflowContract` (in [`test_n_runs.py`](../tests/test_n_runs.py)) | pytest (already in the 572 count) | Every PR - locks the workflow YAML against silent regression | $0 |
| Nightly safety regression (N=10 on adversarial samples 04–07) | **DISABLED 2026-05-30 (#336)** — schedule removed + `if: false` on the job. The 2026-05-30 run failed on stochastic noise (sample-04 + sample-05 missed 95% pass-rate because one out of ten runs is 10% of the distribution, wider than the threshold permits). See `.github/workflows/nightly-safety-regression.yml` header for re-enable instructions (either N=20 or threshold=0.90). | $0 while disabled; ~$0.12 per fire if re-enabled |
| `scripts/run_pipeline_demo.py` | **Manual** | You decide. Not CI-triggered. | ~$0.005 per deep run; cache may zero it |
| `scripts/run_api.py` | **Manual** | You decide. Long-lived process serving the UI + API. | ~$0.005 per pipeline run via UI |
| `scripts/run_integration_smoke.py` | **Manual** | You decide. Defaults to `--no-cache` because its purpose is live verification, not replay. | ~$0.03 per full 7-sample sweep |

## Why the live eval gate is off

The live eval makes ~24–30 sequential Gemini calls. On GitHub-hosted runners
those calls hit a backend pool that regularly returns 500 INTERNAL / 503
UNAVAILABLE "high demand" errors (see [ADR-0007](adr-0007-gemini-3-5-flash-switch.md):
the same model is fine from a laptop — Google routes runner IP ranges to a
congested pool). Retries don't help: a spike outlasts them, and worse, a
**failed** run is not free — every call that succeeds before the failing one
is billed, and each retry re-pays for them. PR #340 re-enabled the gate
on push-to-main to test whether the new path filter + retry classifier
would be enough; PR #348 turned it back off after the first runs proved
the runner-pool weather is the dominant signal regardless of mitigations.

The gate's structural coverage (cache disabled, path filter, threshold
+ budget enforcement, retry classifier) is preserved in the YAML so a
self-hosted runner could flip the `if: false` to a real condition and
re-activate it without re-architecting. `test_ci_cache_disable.py`
locks the YAML shape so the contract can't silently drift.

In the meantime, the **manual + local** run is the live quality signal
(`uv run python evals/run_evals.py --no-cache` from a laptop where the
runner-pool 5xx don't hit). CI gates only on fast, free, deterministic
tests via the quality job.

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
        │    └─ pytest -q (572 tests, includes CI-contract assertions)
        │
        └─ regression job               ← DISABLED (if: false)
             │                            preserved as a flip-switch only
             ├─ run_evals.py (Gemini, 7 samples)         would-be ~$0.03
             ├─ check_thresholds.py (per-metric floor gate)
             └─ check_budgets.py (cost + latency gate)
```

CI today gates only on the quality job (~30 s, $0). The regression job's
path filter, retry classifier, and budget gates are kept intact in the
YAML so the gate can flip back on (e.g. on a self-hosted runner) without
rewiring.

## Visual - a 24-hour timeline (no human action)

```
00:00 UTC  ──┐
             │
02:00 UTC  ──┤   nightly-safety-regression.yml
             │   ── DISABLED 2026-05-30 (#336) ──
             │   no Gemini calls fire from this workflow;
             │   schedule trigger removed, job `if: false`.
             │   Would-be cost when re-enabled: ~$0.12 / fire.
             │
06:00 UTC  ──┤
   …         │
             │
23:59 UTC  ──┘
```

While the nightly is off, the adversarial samples (04–07) still get a
single-pass measurement whenever the **local** `evals/run_evals.py` is
invoked. ADR-0014 documents the original trade-off: nightly N=10 was
preferred over per-PR N=10 to keep PR cost low; the 2026-05-30 failure
showed the N=10 / 95%-threshold combination doesn't tolerate one
stochastic miss out of ten runs. Re-enabling needs either N=20 or
threshold=0.90 (see the workflow file header).

## What's NOT automated

- **`run_api.py`** + **`run_pipeline_demo.py`** + **`run_integration_smoke.py`** - pure on-demand. CI never invokes them. They exist for **you** to validate things CI can't: interactive UX, exploratory runs, ad-hoc smoke checks.
- **Adversarial samples on PRs** - get only N=1. N=10 happens nightly. Adversarial regressions have up to 24h detection window.
- **Live LLM behaviour drift between Gemini versions** - would surface in the nightly first, the next PR's regression second.
- **The full-local Ollama path** - code-tested via unit tests, but the runtime deepeval ↔ ChatOllama wire is not exercised in CI (no Ollama on CI runners). Verified manually by the operator when they flip `RTIA_LLM_PROVIDER=ollama` + `RTIA_OLLAMA_JUDGE=1`.

## Mental shortcut

| If you… | …this runs |
|---|---|
| Push any commit | pre-commit + 572 pytest |
| Push a PR touching `agents/`, `prompts/`, or `evals/` | quality job only ($0) — live eval gate disabled (#348) |
| Push a PR touching only docs or tests | quality job only ($0) |
| Want a live quality signal | `uv run python evals/run_evals.py --no-cache` locally (~$0.03, you) |
| Want to see the UI behave with real input | `scripts/run_api.py` (you) |
| Want a one-shot pipeline run | `scripts/run_pipeline_demo.py` (you) |
| Want to confirm nothing broke structurally end-to-end | `scripts/run_integration_smoke.py` (you) |
| Want to re-baseline the eval suite intentionally | `uv run python evals/run_evals.py --no-cache` (you) |

## Why this shape - the testing pyramid (today)

```
                         ▲
                        ╱ ╲          off-CI, manual / local
                       ╱   ╲         live eval + adversarial probes
                      ╱     ╲        (operator runs on demand, $0.03–$0.12)
                     ╱───────╲
                    ╱         ╲      DISABLED (if: false)
                   ╱  Gemini   ╲     preserved as a flip-switch:
                  ╱  eval gate  ╲    if a self-hosted runner ever lands
                 ╱───────────────╲
                ╱                 ╲  $0 per PR
               ╱   pytest 572      ╲ unit + contract + CI-YAML-lock
              ╱   + pre-commit      ╲(fast, always, deterministic)
             ╱───────────────────────╲
```

- **Bottom (fast/free):** every commit, every PR. Mocked LLM, contract assertions, lint. Catches structural breakage in seconds. **This is the only layer CI gates on today.**
- **Middle (would-be measured/cheap):** the live eval gate exists in YAML but is `if: false`. ADR-0007 explains the runner-pool 5xx weather that makes it unworkable on GitHub-hosted runners.
- **Top (would-be nightly/thorough):** the nightly safety regression workflow is also disabled (`if: false` + no schedule). Re-enabling needs N=20 or threshold=0.90 to absorb single-run stochastic noise.

In practice, the **manual layer** (`scripts/run_*.py` + the local
`run_evals.py`) is doing the work the middle and top tiers used to. CI
is the deterministic safety net; the operator is the quality gate.

## Cross-references

- [tests/README.md](../tests/README.md) - what each of the 40 test files actually covers
- [docs/adr-0013-llm-response-cache.md](adr-0013-llm-response-cache.md) - why the regression job disables the cache (belt-and-suspenders)
- [docs/adr-0014-stochastic-ac-validation.md](adr-0014-stochastic-ac-validation.md) - why nightly cadence not per-PR for N=10
- [docs/USAGE.md](USAGE.md) - daily-driver guide, including §10 on full-local mode
