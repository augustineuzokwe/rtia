# ADR-0013: LLM response cache — short TTL, CI no-cache, prompt-hash key

**Status:** Accepted (2026-05-26)
**Author:** augustineuzokwe
**Decision driver:** Iteration on RTIA (UI/exporter changes, local refactors) re-runs the same inputs repeatedly; without a cache, each iteration pays Gemini Flash for output the user already saw. The cache is a real win — but if we ship the wrong cache defaults, we ship something worse than no cache: silent eval-gate dishonesty. This ADR captures the design that gets both the win and the honesty.

## Context

Two real failure modes if we get this wrong:

1. **"False-green CI" trap.** Someone runs the eval suite, gets a green result, the cache stores it. Days later they "verify" the eval still passes — the cache hits, CI is instant green. They ship the change. Reality: the model's behaviour has drifted in the interim, but the eval never re-measured because the cache was warm. The PR-status gate gave confidence it hadn't earned. *Worse than no eval gate because it actively misleads.*

2. **Stale-prompt amplification.** A prompt edit lands; the cache key doesn't include the prompt; the cache replays the old prompt's output; the eval gate measures the old prompt's behaviour. The PR shows "no metric change" even though the prompt fundamentally changed. Identical end-result to (1) but triggered by a different mechanism.

Promptfoo's cache design ([docs](https://www.promptfoo.dev/docs/configuration/caching/)) was the starting reference. RTIA adopts the *shape* but not the *defaults* — specifically, Promptfoo's 14-day TTL is too long for our iteration cadence.

## Decision

**Disk-backed response cache** at `~/.rtia/cache/` (overridable via `RTIA_LLM_CACHE_DIR`), keyed on `sha256(model_id + prompt_hash + canonicalised_messages)`, with the following non-negotiables:

| Defence | How it's enforced |
|---|---|
| **Prompt edits auto-invalidate** | `prompt_hash` from `agents/config.py:prompt_hash()` is part of the cache key. A one-character change to a prompt module changes the hash, the key, and the lookup — next call hits the model live. |
| **Provider/model swaps auto-invalidate** | `model_id` is part of the key, formatted as `"google:gemini-3.5-flash"` or `"ollama:llama3.1:8b"`. Cross-provider cache collisions are impossible. |
| **Short TTL** | Default `RTIA_LLM_CACHE_TTL=86400` (24 h). Worst-case stale window is one workday — not Promptfoo's 14 days. |
| **CI always disables** | `.github/workflows/ci.yml` regression job sets `RTIA_LLM_CACHE=disabled` AND passes `--no-cache` to `evals/run_evals.py`. Belt and suspenders so the disable survives a future workflow refactor that might strip one of the two. |
| **Re-baselining always disables** | Plan §7.0 pattern: re-baselining runs without `--no-cache` would defeat the whole purpose. The eval runner's `--no-cache` CLI flag is the documented escape hatch. |
| **Integration smoke defaults to disabled** | `scripts/run_integration_smoke.py` exists to verify live behaviour; an `--use-cache` opt-in flag exists for the rare case where you want it. |

### Per-workflow defaults

| Workflow | Cache default | Why |
|---|---|---|
| `scripts/run_pipeline_demo.py` | ON | Iterating UI/exporters; re-runs same input often; biggest cost win. |
| API `POST /pipeline` (`scripts/run_api.py`) | ON | Same reason. |
| `evals/run_evals.py` from local interactive | ON | Fast feedback during refactors; prompt edits auto-invalidate via `prompt_hash` so it can't lie. |
| `evals/run_evals.py` from CI regression job | **OFF** | "False-green CI" trap. Non-negotiable. |
| `scripts/run_integration_smoke.py` | **OFF** | Smoke exists for live verification, not replay. |
| Re-baselining (manual, plan §7.0) | **OFF** | Whole point is fresh measurement. |
| Adversarial / safety regression runs | **OFF** | Distribution matters — want N draws, not 1 cached draw. (Future `--n-runs` work, see Issue #233.) |

### Env-var contract

- `RTIA_LLM_CACHE=enabled|disabled` — case-insensitive, default `enabled`.
- `RTIA_LLM_CACHE_TTL=<seconds>` — positive integer, default 86400. Garbled values fall back to the default rather than crashing.
- `RTIA_LLM_CACHE_DIR=<path>` — supports `~` expansion. Default `~/.rtia/cache`. Created on first use.

### CLI-flag contract

- `--no-cache` on `scripts/run_pipeline_demo.py` and `evals/run_evals.py` — sugar for `RTIA_LLM_CACHE=disabled` for the lifetime of the process.
- `--use-cache` on `scripts/run_integration_smoke.py` — inverted because the script defaults to disabled.

## Alternatives considered

- **Promptfoo's `keyv-file` adapter** with their 14-day TTL default. Rejected: too long; the cost of a stale measurement on our iteration cadence is higher than the cost of a re-run.
- **In-memory LRU** instead of disk. Rejected: cache dies on process exit, killing the local-iteration win for any pattern that involves rebooting the API or relaunching the demo.
- **No cache at all.** Rejected: the iteration cost is real, and the safe-cache design is not complicated.
- **Cache by model + messages only (no `prompt_hash`).** Rejected: this is the failure mode #2 above. Cache would silently replay old prompt behaviour after a prompt edit.
- **Make the cache opt-in.** Rejected: the win is for everyday paths; making them opt-in means most adopters never see it.

## Consequences

### Positive

- Local iteration on UI/exporters/refactors costs ~$0 after first warm-up of a given input.
- The "false-green CI" trap is eliminated by the env + CLI double-disable on the regression job.
- A prompt edit cannot silently replay old behaviour — the `prompt_hash` is the load-bearing invariant.
- Cross-provider cache collisions are impossible — model_id includes the provider prefix.

### Trade-offs

- **Adds `diskcache` as a runtime dep.** Pure Python, no native deps, single-file storage, small surface area. Cost is acceptable.
- **First production run after a code refactor is the same cost as today.** Cache benefit only kicks in on the second and later runs of the same input.
- **24-hour TTL means batched experiments across days can't reuse a cache across a day boundary.** Acceptable: extending the TTL would re-introduce the false-green window.
- **CI regression cost is unchanged.** This ADR explicitly preserves it that way; do not "optimise" by removing the CI disable.

## How this ships

- `agents/_llm_utils.cached_invoke()` is the single entry point.
- All five production agents route their `llm.invoke(messages, config=…)` through `cached_invoke()`.
- `tests/test_llm_cache.py` asserts every invariant in the table above. New cache behaviour requires a new test in that file.
- `tests/test_ci_cache_disable.py` parses `.github/workflows/ci.yml` and asserts the regression job environment includes `RTIA_LLM_CACHE: disabled` and the eval command includes `--no-cache`. A future CI refactor that drops either will fail this test.

## References

- [Issue #230 — high-priority Task with full acceptance criteria](https://github.com/augustineuzokwe/rtia/issues/230) — this ADR codifies what that issue's "three-part fix" looked like in code.
- [Promptfoo caching docs](https://www.promptfoo.dev/docs/configuration/caching/) — the reference design we adopted in shape and deliberately deviated from in TTL.
- [`agents/config.py:prompt_hash`](../agents/config.py) — the existing utility this design builds on.
- [`docs/pipeline-baseline-2026-05-26.md`](pipeline-baseline-2026-05-26.md) — fresh baseline created on the explicit assumption of a cache-free measurement; future re-baselines must continue that discipline.
