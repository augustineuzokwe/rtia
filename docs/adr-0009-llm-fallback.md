# ADR-0009: LLM fallback policy — fail fast with structured error, never silent model fallback

**Status:** Accepted (2026-05-22)
**Author:** augustineuzokwe
**Decision driver:** Phase 12.5 of the prod-readiness roadmap — establish what the pipeline does when an LLM call exhausts the retry budget set by [ADR-0003](adr-0003-llm-resilience.md), and explicitly forbid the silent-model-fallback failure mode.
**Complements:** [ADR-0003](adr-0003-llm-resilience.md) (retry policy: 5 retries, exponential backoff, ~15.5s patience). ADR-0003 owns the retry-side. ADR-0009 owns what happens *after* retries exhaust.
**Numbering note:** The road-to-production plan referenced this as `adr-0003-llm-fallback.md`. ADR-0003 was already taken by the resilience policy. Used the next available number (0009). Same naming-collision pattern as [ADR-0008](adr-0008-pii-langsmith.md) (which the roadmap called `adr-0002`).

## Context

ADR-0003 settled the retry side: 5 retries with exponential backoff (~15.5s total patience budget) for transient Gemini failures (503/UNAVAILABLE, 429/RATE_LIMITED, 5xx server errors, plus `x-should-retry` responses). That works for brief load spikes — and we have observed it working under exactly those conditions (the GitHub-runner-pool-specific 503 storm that drove [ADR-0007](adr-0007-gemini-3-5-flash-switch.md)).

What happens when 5 retries are not enough? Today, the underlying Gemini SDK exception (`google.genai.errors.ServerError`, `ClientError`, or a transport-level `TimeoutError`/`ConnectionError`) propagates out of LangChain's `llm.invoke()` and up through the agent's library function. The caller — currently `pipeline.invoke()` in the demo or in tests — receives an uncaught exception and crashes. No artifact is returned. The operator sees a stack trace.

Two failure-handling choices then arise, and the rest of this ADR is about which one we pick:

1. **Silent fallback** — on retry exhaustion, transparently invoke a different model (e.g. fall back from `gemini-3.5-flash` to `gemini-2.5-flash`) and return its output as if nothing had happened.
2. **Fail fast with structured error** — convert the underlying exception into a structured form, attribute it to the failing agent, and produce a `FinalUserStory` whose sections are empty and whose `metadata["error"]` carries the failure detail as JSON.

## Verified facts (2026-05-22)

- Gemini SDK exception classes via `google.genai.errors`:
  - `APIError` (base) — exposes `code: int` (HTTP status) and `message: str`
  - `ClientError` (4xx) — extends APIError
  - `ServerError` (5xx, includes the 503s we hit) — extends APIError
- LangChain re-raises these classes unchanged from `llm.invoke()`.
- The Gemini SDK's internal retry exhausts after `max_retries` attempts (RTIA sets this to 5 in `agents/config.py`) regardless of whether the underlying failure is retryable.
- `FinalUserStory.metadata` is typed as `dict[str, str]` — a JSON-encoded string value fits the existing schema without a contract change.

## Decision

### 1. No silent model fallback

If `gemini-3.5-flash` exhausts its retries, the pipeline does NOT automatically invoke `gemini-2.5-flash` or any other model. Operator can choose to change `DEFAULT_MODEL` in a code change or override the agent's `model` kwarg explicitly — but the pipeline never makes that choice transparently.

### 2. Fail fast with structured error

Every agent's library function (`analyze_requirement`, `write_user_story`, `generate_acceptance_criteria`, `write_test_cases`, `review_artifact`) wraps its `llm.invoke()` call in `try/except` and converts any exception to `LLMPipelineError` via `wrap_llm_exception` (see `agents/_llm_errors.py`). The agent's name is pinned at the exception site so the failure is unambiguously attributed.

### 3. Structured error shape

```python
@dataclass(frozen=True)
class LLMFailureDetail:
    agent: str                      # e.g. "requirements_analyst"
    error_class: str                # e.g. "ServerError"
    http_status: int | None         # 503, 429, None for transport errors
    message: str                    # capped at 500 chars
    retries_attempted: int          # from agents.config.DEFAULT_MAX_RETRIES
    occurred_at: str                # ISO-8601 UTC
```

JSON-encoded into `FinalUserStory.metadata["error"]` by `build_stub_artifact_from_error` in `agents/graph.py`.

### 4. Stub artifact on failure

When the demo (or any future API entry point) catches `LLMPipelineError`, it calls `build_stub_artifact_from_error(exc)` to produce a `FinalUserStory` with:

- Empty `description` and `objective` carrying a placeholder string explaining the failure
- Empty `acceptance_criteria` and `test_cases`
- `metadata["error"]`: the JSON-encoded `LLMFailureDetail`
- `metadata["review_summary"]`: a human-readable one-liner so the rendered markdown surfaces the failure inline

This keeps the failure path symmetrical with the success path: both produce a `FinalUserStory` that can be rendered, persisted, or returned over an API. The caller never has to choose between "got an artifact" and "got an exception" — it always gets an artifact and inspects metadata.

### 5. Demo exit codes

Three distinct outcomes:

| Exit code | Cause | Where decided |
|---|---|---|
| 0 | Successful pipeline run | Default `sys.exit(0)` at end of `main()` |
| 2 | Security block (12.3 secret in input, 12.4 prod tracing misconfig) | `SecretInInputError`, `ProductionTracingError` |
| 3 | LLM unavailability after retry exhaustion | `LLMPipelineError` |

CI scripts and shell pipelines can distinguish "ran successfully" from "ran but the LLM was unavailable" from "refused to run for security reasons" without parsing stderr.

## Alternatives considered

### A. Silent fallback to a different model (rejected)

Transparently retry on a different model when the configured one exhausts retries. Rejected because:

- **Provenance becomes ambiguous.** A LangSmith trace recorded `gemini-3.5-flash` as the model, but the actual output came from `gemini-2.5-flash`. Debugging future regressions becomes a guessing game.
- **Eval discipline breaks.** [Baselines](../evals/baselines.md) are pinned to a specific model + prompt hash. A silent fallback corrupts the attribution: regressions on the fallback model would attribute to the primary model.
- **Failure modes get masked.** The whole point of Phase 12 hardening is to make failures legible. Silent fallback hides exactly the operational signal we need (the LLM is having a problem).

### B. Hard crash with no structured error (rejected)

Let the underlying SDK exception propagate as-is. Rejected because:

- The caller (current: demo script; future: API handler) has to handle every possible SDK exception type, with no canonical shape.
- No artifact is produced — debugging the pipeline state at the moment of failure is harder than it needs to be.
- A future UI gets a stack trace instead of a structured error it can render.

### C. Stub artifact with no structured error in metadata (rejected)

Produce a `FinalUserStory` with empty sections but no error metadata. Rejected because:

- The caller has no way to distinguish a real artifact with empty sections (early-pipeline-development edge case) from a failed run.
- LangGraph state checkpoints lose the failure attribution — the operator looking at a paused run can't tell *why* it's empty.

### D. Per-agent fallback policies (rejected)

Let each agent define its own fallback (e.g. the Story Writer falls back to a smaller model, the Reviewer just skips). Rejected because:

- Too many policy points to keep track of. Same architecture would re-emerge as ten different decisions over time.
- The structured-error path is uniform; per-agent variation would re-introduce the silent-fallback problems for whichever agents opted in.

## Consequences

**Positive**

- The demo (and any future caller) gets a predictable artifact shape regardless of whether the pipeline succeeded or failed at the LLM layer.
- LangSmith traces, eval reports, and debugging UIs can show "this run failed at agent X with code 503" without parsing stack traces.
- The "no silent model fallback" rule is written down. A future contributor cannot accidentally introduce it as a "robustness improvement" — they would be reverting a documented decision.
- Exit code differentiation (0/2/3) gives CI scripts a clean way to react to each outcome.

**Negative**

- Every LLM call site now has a try/except wrap. Five agent files touched. Mitigation: the wrap is two lines and references `wrap_llm_exception` for the policy.
- Future agents must follow the same pattern. Mitigation: the pattern is referenced in `agents/_llm_errors.py`'s module docstring and in this ADR.
- The stub artifact is rendered with placeholder text, which a downstream consumer might mistake for legitimate content if they ignore `metadata["error"]`. Mitigation: the placeholder strings explicitly say "Pipeline aborted before this section was written. See metadata.error." A consumer that ignores the metadata is acting in bad faith and would have problems regardless.

## Migration

- No env-var changes. No `.env.example` update needed.
- Existing tests pass unchanged — the wrappers are no-ops on successful invocations.
- New tests in `tests/test_llm_errors.py` cover the exception mapping and the stub-artifact shape.
- CI's regression eval gate is unaffected (eval calls don't go through the demo's catch path; if an eval run hits a real 503, the test fails loudly as before — that's the right CI behaviour).

## Re-verification

- Re-check at the start of each Phase 12 sub-phase that the no-silent-fallback policy still holds. Specifically: 12.6 (SECURITY.md) and 12.7 (GUARDRAILS.md) should cite this ADR.
- Re-check at the start of Phase 14 (UI/API). The structured error is the contract the API will surface to clients — pin it before the API freezes.
