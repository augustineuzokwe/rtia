# ADR-0011: MIT License for v1

**Status:** Accepted (2026-05-23)
**Author:** augustineuzokwe
**Decision driver:** Phase 16.1 of the prod-readiness roadmap - a public-ready repo needs an explicit license. Shipped in [PR #168](https://github.com/augustineuzokwe/rtia/pull/168) without an accompanying rationale doc; this ADR fills that gap.
**Numbering note:** The road-to-production plan listed this as `adr-0004-license-choice.md`. By the time Phase 16 landed, `adr-0004` had been taken by the FinalUserStory contract ADR. Used the next available number (0011). Same naming-collision pattern as [ADR-0008](adr-0008-pii-langsmith.md) and [ADR-0009](adr-0009-llm-fallback.md).

## Context

RTIA is a workshop project: the codebase is the artifact, but the durable deliverable is the learning. Maximising adoption - by workshop participants, by reviewers, by anyone evaluating the agent-orchestration patterns later - matters more than retaining patent-licensing leverage or copyleft propagation.

Two realistic options:

1. **MIT** - short, permissive, no patent grant, no copyleft. Universally understood.
2. **Apache-2.0** - permissive with an explicit patent grant, slightly more lawyer-friendly for corporate consumers.

## Decision

Adopt **MIT**, dated 2026, copyright holder = Augustine Uzokwe.

Rationale:

- **Workshop ergonomics** - participants forking the repo to follow along shouldn't have to think about the license. MIT is the lowest-friction signal that "this is permissive."
- **Patent grant** is not a meaningful concern. RTIA is a glue project over LangGraph / LangChain / Gemini SDKs; the novelty is the agent-orchestration pattern, not patentable subject matter. Apache-2.0's patent clause carries no value here.
- **Short text** - the LICENSE file is one screen. Easier to read than the Apache-2.0 boilerplate.
- **Compatible with the upstream stack** - MIT is permissive enough to combine freely with the Apache-2.0 / MIT-licensed dependencies RTIA pulls in.

## What this does not change

- Existing copyright headers in source files - none added; the LICENSE file at the repo root is sufficient under MIT.
- Third-party dependency licenses - each retains its own license; the project's MIT terms apply only to RTIA's own code.

## Consequences

**Positive:**
- Anyone who finds the repo can fork, modify, and ship without further questions.
- The license file is short enough that contributors actually read it.

**Trade-offs:**
- No explicit patent grant. If RTIA's orchestration patterns ever become patentable subject matter (unlikely - see rationale above), Apache-2.0 would retroactively offer better legal hygiene.
- No copyleft. A future commercial fork could close-source improvements. Acceptable: the workshop's value is in the public learning artifact, not in propagating openness through downstream forks.

## References

- [LICENSE](../LICENSE) - the file this ADR justifies.
- [PR #168](https://github.com/augustineuzokwe/rtia/pull/168) - Phase 16.1–16.3 scaffold that shipped the LICENSE.
