# ADR-0008: No LangSmith tracing in production

**Status:** Accepted (2026-05-22)
**Author:** augustineuzokwe
**Decision driver:** Phase 12.4 of the prod-readiness roadmap - the road-to-production plan calls for a documented PII/LangSmith policy before Phase 14 lands a UI/API and real customer data starts flowing through the pipeline.
**Numbering note:** The road-to-production plan referenced this as `adr-0002-pii-langsmith.md`. ADR-0002 was already taken by the durable-checkpointer decision. This ADR uses the next available number (0008). The Phase 12.4 task carries the numbering deviation in its issue body for traceability.

## Context

RTIA's agents transmit raw requirement text to two third parties:

1. **Google AI Studio (Gemini)** - every LLM call, mandatory for the pipeline to function. Google retains prompts and responses per their published policy.
2. **LangSmith** - when `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` is set, every LLM call plus the intermediate agent reasoning is persisted to LangSmith's logs. Optional. Used in development and CI for debugging.

Requirement text legitimately contains PII. A real user story might read:

> *"As Alice Bakker, a customer from Amsterdam who signs up via the mobile app, I want to receive my receipts at alice.bakker@example.com so that I can keep them with my expense reports."*

Alice's name, city, and email are **part of the requirement, not a leak**. The story does not parse without them - the role identification ("As Alice...") is structurally load-bearing. Scrubbing PII on input would damage the requirement; the LLM would receive a different text than the user intended, and the artifact would no longer match the input.

This is fundamentally different from Phase 12.3's secret-regex policy. A leaked API key is unambiguous: not part of the requirement, never legitimate, blocked outright. PII is the opposite: routinely legitimate, irreducible without damage.

### What we cannot prevent

- The text travels to Gemini regardless. Google's retention is a vendor contract decision, not something RTIA can enforce. Customers using RTIA in production must accept the Gemini ToS or replace the provider (out of scope here; see ADR-0006 for the provider-choice context).
- This ADR does not solve Gemini transit. It solves the **secondary** persistence to LangSmith.

### What we can prevent

LangSmith is optional. Tracing is controlled by `LANGSMITH_TRACING` + `LANGSMITH_API_KEY` env vars. A production deployment that forgets to disable tracing accidentally creates a second copy of customer requirement text on a SaaS we don't own, with no PII-aware indexing, retention policy, or access control beyond LangSmith's defaults.

A misconfigured production deployment is the realistic failure mode. The fix must be at process startup, not in a runtime check, because once the first trace ships the data is exfiltrated.

## Verified facts (2026-05-22)

- `agents/observability.py:tracing_status()` is the single point where LangSmith env vars are inspected. Currently it reports `enabled=True` iff `LANGSMITH_TRACING` is truthy AND `LANGSMITH_API_KEY` is present.
- LangChain's auto-instrumentation reads the env vars directly at LLM-call time. There is no in-process toggle to disable tracing once `LANGSMITH_TRACING=true` is set in the environment - the only reliable disable is to ensure the env var is not truthy before the first LLM call.
- The demo, eval runner, and (future) API entry point all read the env on startup. None of them currently check whether the deployment is production-marked.

## Decision

**No LangSmith tracing in production. RTIA refuses to start when both `RTIA_ENV=production` and `LANGSMITH_TRACING` is truthy.**

Concretely:

1. New env var **`RTIA_ENV`** with three allowed values: `development` (default when unset), `ci`, `production`.
2. **`agents/observability.py`** gains:
   - `tracing_status()` returns `enabled=False, reason="forced off by RTIA_ENV=production"` whenever `RTIA_ENV=production`, regardless of what `LANGSMITH_TRACING` says. This is a defence-in-depth read - even if the assertion below is bypassed somehow, `enabled=False` is still reported and any code that conditions on it sees tracing as off.
   - New `assert_safe_for_env()` helper. Called by every process entry point (demo, future API). Raises `ProductionTracingError` if both `RTIA_ENV=production` and `LANGSMITH_TRACING` is truthy. The process exits with a clear error before any LLM call.
3. **Hard assert, not soft override.** A silent override would hide a real configuration drift - an operator might never know they shipped a misconfigured env to a prod-marked instance, and the same misconfig would then ship to other environments without anyone noticing. Refusing to start is loud feedback. Consistent with the Phase 12.3 philosophy ("block before external call") and with how the secret regex policy was upgraded from warn to block (#124).

## Alternatives considered

### A. PII regex scrubbing on input (rejected)

Scrub names, emails, phone numbers, addresses from the requirement text before sending to LLMs. Rejected because:

- The requirement domain is too tangled with named users/places/identifiers to scrub without damaging legitimate content. Alice Bakker as a user-story role is not a leak; she's the story's actor.
- Any scrubber misses some patterns. You find out by leaking. The cost of false-negative leakage is high; the cost of false-positive scrubbing is silent requirement corruption.
- Scrubbing creates a divergence between what the user sees and what the LLM sees. The artifact would no longer match the input. Debugging becomes impossible.

### B. Self-hosted LangSmith (rejected for now)

Run LangSmith on infrastructure under our control. Rejected as out-of-scope for Phase 12: requires a separate cost/effort evaluation, deploys before we have a production environment to deploy to, and doesn't change the underlying principle (the policy below would still apply to the self-hosted instance - *that* tracing is also off by default in production until a PII-aware logging layer is built).

### C. Opt-in-per-run tracing (rejected)

Let operators tag specific prod runs as "OK to trace" via a flag. Rejected because it puts the decision in the wrong hands - a developer triaging a prod incident at 2am should not be the one deciding whether the customer's requirement text gets persisted to a third-party SaaS. That's a privacy decision, not a debugging decision. The infrastructure must default to safe.

### D. Scrub patterns at the LangSmith client layer (rejected)

Intercept the LangChain LangSmith client to scrub PII patterns from traces before transmission. Rejected for the same reason as A: any scrubber misses some patterns. The downside is the same (leakage) and the upside (some traces are useful) is dwarfed by the privacy cost.

## Consequences

**Positive**

- A misconfigured production deployment cannot exfiltrate requirement text to LangSmith. The process refuses to start before any LLM call.
- One env var (`RTIA_ENV`) carries the whole policy. No new config file, no Vault entry, no out-of-band coordination.
- Development workflow is unchanged. Developers continue to set `LANGSMITH_TRACING=true` in `.env` and get traces; the new `RTIA_ENV` defaults to `development` when unset.
- CI workflow is unchanged. CI runs are non-customer-facing; LangSmith tracing in CI surfaces real signal during debugging and is explicitly allowed by `RTIA_ENV=ci`.

**Negative**

- Production loses LangSmith-based observability. When Phase 14 ships and prod tracing is genuinely needed for incident triage, we will need a separate PII-aware logging layer (structured, not free-text reasoning, with explicit PII redaction at log-time). This is a follow-up issue not in scope here.
- An operator who wants to debug a prod issue cannot get a trace without first violating the policy. This is the intended friction.
- The hard assert is brittle if someone wires up RTIA into an automation pipeline that does not run a Python process per request (e.g. a long-lived worker process). For v1 this is not a concern - the architecture is per-request invocation - but a future serverless deployment that prewarms workers would need to ensure the env is asserted on container start.

## Migration

- Existing developers: no change required. `RTIA_ENV` defaults to `development` when unset.
- Existing CI: no change required. CI workflow may optionally set `RTIA_ENV=ci` to make intent explicit; behavior is unchanged either way.
- Production deployments (future, Phase 14+): MUST set `RTIA_ENV=production`. MUST NOT set `LANGSMITH_TRACING=true`. The process will refuse to start if both are set.

## Re-verification

- Re-check at the start of each Phase 12 sub-phase that the policy still holds. Specifically: 12.6 (SECURITY.md) and 12.7 (GUARDRAILS.md) should cite this ADR.
- Re-check at the start of Phase 14 (UI/API). The prod observability replacement (a separate PII-aware logging layer) is a Phase 14 dependency to call out in that phase's planning.
