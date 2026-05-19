# ADR-0003: LLM resilience policy (retry, timeout, fallback)

**Status:** Accepted (2026-05-19)
**Author:** augustineuzokwe
**Decision driver:** Phase 2.3 of the prod-readiness roadmap. The Plan-agent's pre-implementation critique flagged "LangChain defaults are not production-safe." Verification before implementation showed this was partially aspirational — the Anthropic SDK has prod-grade retry behavior already — but the patience window needed tuning and the policy was undocumented.

## Verified facts (2026-05-19)

Checked `anthropic._base_client.BaseClient._should_retry` and `_calculate_retry_timeout` directly. The SDK retries on:

| Status code | Reason          |
|------------:|-----------------|
| 408         | Request timeout |
| 409         | Lock timeout    |
| 429         | Rate limit      |
| ≥500        | Server errors — includes **529 Overloaded** that Claude Opus 4.7 emits periodically |

Plus: any response with header `x-should-retry: true` (server explicitly requests retry). Backoff:

```
sleep_seconds = min(0.5 * 2^n, 8.0)
sleep_seconds *= jitter   # +/- ~0.5s
```

If the server sends a `Retry-After` header (≤60s), that value wins over the backoff math.

Anthropic SDK default `max_retries` is `2`.

## Decision

### Retry policy

`agents/config.py` sets `DEFAULT_MAX_RETRIES = 5` (was 3).

**Patience budget:**

| max_retries | Total wait (no jitter) | Notes                                             |
|------------:|-----------------------:|---------------------------------------------------|
| 2 (SDK)     | 0.5 + 1.0 = 1.5s       | Tight; misses brief load events                   |
| 3 (prior)   | 0.5 + 1.0 + 2.0 = 3.5s | Insufficient for Opus 529 storms                  |
| **5 (now)** | **15.5s**              | Rides out brief storm; fails fast on sustained    |
| 10          | 47.5s                  | Batch/eval jobs that can wait                     |

The `+ 4 + 8 + 8` (cap kicks in) means each additional retry beyond 5 adds ~8s of wait. Diminishing returns past 7–8 retries for an interactive workflow.

**Per-call override:** every agent function exposes `max_retries: int = DEFAULT_MAX_RETRIES` as a keyword argument. Tighten for SLO-bound endpoints; loosen for batch evals.

### Timeout policy

`DEFAULT_TIMEOUT_SECONDS = 60.0`. This is the wall-clock per-request cap, applied via `httpx.Timeout`. Opus 4.7 with extended thinking typically completes in <30s for our prompt sizes; 60s gives 2x headroom. **No evidence to change**; revisit if telemetry from Phase 13.2 shows p95 trending above 30s.

### Circuit breaker

**Not implemented.** A stateful "open after N failures, cool down" circuit is overkill for a single-user dev tool. The SDK's exponential backoff is effectively a per-request circuit breaker. Revisit only if/when hosted multi-user deployment lands and we observe error storms that would benefit from cross-request memoization.

### Fallback model strategy

**Not in this PR; deferred to Phase 12.5.** When Opus retries exhaust, the v1 behavior is "fail fast with structured error in `FinalUserStory.metadata.error`" rather than silent retry to Sonnet/Haiku. Reasoning: switching models mid-pipeline silently changes eval baselines and obscures cost telemetry. Failing loudly is more honest at v1 scale.

## Consequences

**Positive**
- Sustained 529 storms (observed during Phase 1.3 verification) now have a chance to clear before the pipeline fails.
- Policy is documented; future readers don't have to re-derive it from SDK source.
- Per-call overrides give every caller (demo, future UI, future eval harness) the right SLO knob.

**Negative / risks**
- A genuinely down Anthropic endpoint now wastes ~15s per call before failing. Acceptable for interactive use; expensive for batch evals running thousands of requirements (those should override to `max_retries=2` or `3`).
- Silent retries are still silent at the SDK layer. LangSmith trace metadata exposes `x-stainless-retry-count` which mitigates this, but the demo banner doesn't surface it. Phase 13.2 (structured logging) will close this gap.

## Followups

- **Phase 12.5**: structured error in artifact metadata when retries exhaust + decision on whether to surface a fallback model option.
- **Phase 13.2**: structured JSON logging exposes retry counts per agent call.
- **Phase 13.1**: cost-budget enforcement uses retry count to attribute "cost spent on retried failures" separately.
- Revisit `DEFAULT_TIMEOUT_SECONDS` if telemetry shows p95 latency trending above 30s.
