# ADR-0015: Fake-LLM provider for deterministic, zero-cost UI testing

**Status:** Accepted (2026-05-28)
**Author:** augustineuzokwe
**Decision driver:** Epic 7 ([#292](https://github.com/augustineuzokwe/rtia/issues/292)) introduces a Playwright E2E suite for the new React UI ([Epic 6, #291](https://github.com/augustineuzokwe/rtia/issues/291)). Running those specs against Gemini in CI would bleed API budget on every PR and produce flaky tests (real LLM output varies run-to-run). Running them against Ollama is free but still non-deterministic and slow (10–20s/call). Neither is fit for CI. This ADR defines a third provider — `fake` — that returns canned JSON fixtures, making UI assertions trivially deterministic and ~$0 / microsecond per call.

## Context

The provider switch already lives in `agents/config.py` ([ADR-0006](adr-0006-provider-switch.md) chose "one switch, two providers" via `RTIA_LLM_PROVIDER=google|ollama`). All five pipeline agents — `requirements_analyst`, `user_story_writer`, `ac_generator`, `test_case_writer`, `reviewer` — branch on `use_ollama()` at `_build_llm()` time, instantiating either `ChatGoogleGenerativeAI` or `ChatOllama`. Same `.invoke(messages)` contract, same `AIMessage` return shape, same Pydantic-validated parse downstream.

The seam is exactly where a third provider should slot in.

Three reasons not to fold this into Epic 7:

1. **Ownership boundary.** Epic 7 is delegated to the user's Playwright agent team (TypeScript / DOM analysis). Fake-LLM is Python `agents/` code. Mixing them confuses the hand-off.
2. **Reuse beyond E2E.** A canned-response LLM is useful for unit tests (cleaner than 5 layered `unittest.mock.patch` calls — see `feedback_mock_class_per_module`), manual demos that don't burn quota, and debugging agent prompt logic without API calls.
3. **The contract is small enough to be its own thing.** Provider, env var, fixture format, one ADR. Closing it cleanly avoids "the Playwright PR also touched `agents/`" review friction.

## Decision

Add `RTIA_LLM_PROVIDER=fake` as a third value next to the existing `google` and `ollama`. When set:

- A new `agents/_fake_llm.py` module exposes a `FakeChatModel` class implementing the minimal LangChain chat-model surface RTIA actually uses: `.invoke(messages, config=...)` → `AIMessage(content=<canned JSON string>)`.
- A new `use_fake()` helper in `agents/config.py` (parallel to `use_ollama()`) returns `True` when the env var equals `"fake"`.
- Each agent's `_build_llm()` checks `use_fake()` *first*, falling through to the existing Google / Ollama branches if not set. The fall-through order preserves the v1.0.0 behaviour: unset env var → Google.
- The fixture library lives at `tests/fixtures/llm/<scenario>/<agent_name>.json`. Each file's JSON content is exactly what the real LLM would have returned as `AIMessage.content` (a JSON string the downstream Pydantic parser already handles).
- A new env var `RTIA_FAKE_SCENARIO` selects which scenario directory to read from. Default: `deep_clean`. Validated set: see "Initial scenarios" below.

The fake provider has **no network access**, **no model load**, and **no temperature** — it's a deterministic lookup table.

### Initial scenarios

| Scenario | What it exercises | UI terminal state |
|---|---|---|
| `deep_clean` (default) | Single-story, no PO ambiguity. Pipeline runs all the way through Analyst → Story Writer → AC Generator → Test Case Writer → Reviewer. | `DONE` |
| `deep_with_po` | Single-story, Analyst surfaces critical ambiguities → PO checkpoint pauses. | `PAUSED_PO` then `DONE` after resume |
| `split` | Multi-story (Analyst returns `implied_stories ≥ 2`). PO checkpoint pauses in split mode with N editable rows. | `PAUSED_PO` then `DONE_SPLIT` |
| `error` | Analyst raises a controlled exception (or returns malformed output) so the UI's Error panel is exercised. | `ERROR` |

This covers every value of `ThreadStatus` the React UI cares about. New scenarios get added by dropping a directory under `tests/fixtures/llm/` and appending its name to the validated set in `agents/_fake_llm.py`.

### How a scenario is consumed

`FakeChatModel` is constructed per-agent with an `agent_name` (e.g. `"requirements_analyst"`). At `.invoke()` time, it reads:

```
tests/fixtures/llm/{RTIA_FAKE_SCENARIO}/{agent_name}.json
```

Returns `AIMessage(content=file.read_text())`. The downstream Pydantic parse runs unchanged.

If the scenario directory or the agent file is missing, the fake raises a clear `FileNotFoundError` naming both the scenario and the agent. Silent fallback to a generic response would mask test setup bugs.

### Why not in-memory dicts?

Fixtures-on-disk are:

- **Editable without a Python release.** A Playwright spec author can add a new scenario by writing JSON files; no agent code touches.
- **Reviewable as diffs.** A bad fixture shows up as a JSON diff in PR review, not a code change.
- **Inspectable from anywhere.** `cat tests/fixtures/llm/deep_clean/requirements_analyst.json` is the source of truth.

The downside is fixtures and Pydantic schemas can drift. A pytest test (US-35 in the implementation epic) loads every fixture and validates it against the matching agent's `OutputModel.model_validate_json()`. Drift breaks CI on the offending fixture's PR, not a downstream agent's PR.

## Cost & latency

- **API spend:** $0 — no network calls.
- **Latency:** ~1ms per `.invoke()` (file read + JSON string return).
- **Disk:** ~10–50 KB per scenario × N scenarios. Trivial.
- **RAM:** unchanged from Google path; the fake holds no model.

Compared to:
- Gemini Flash: ~$0.005 / pipeline run, ~30s end-to-end, non-deterministic.
- Ollama llama3.1:8b: $0, ~60s end-to-end, non-deterministic, ~5GB disk for the model.
- Fake: $0, ~50ms end-to-end, **identical bytes every run**.

For the Playwright CI use case, the fake provider isn't just cheaper — it's the only option where a failing test reliably means "the UI is broken" rather than "the LLM emitted slightly different JSON than the assertion expected."

## Out of scope for v1.1.0

These follow naturally but are not built in this epic:

- **Streaming.** `.stream()` is not used anywhere in RTIA today (all agents call `.invoke()`); the fake does not implement it. If a future agent needs streaming, the fake yields one chunk of the canned content.
- **Tool calling.** No RTIA agent currently uses LangChain tool calling. If one does later, the fake will need a parallel `tool_calls` field on `AIMessage`.
- **Token usage / cost telemetry.** The fake returns no `usage_metadata`. Real telemetry comes from Gemini / Ollama only.
- **Fuzzing or property-based generation.** Fixtures are hand-authored and reviewed. Generated fixtures could come later if the scenario matrix explodes.
- **Wholesale migration of existing `unittest.mock.patch`-based tests.** US-37 ports *one* test as a proof point; the rest stay until they need touching.

## Migration path

The fake provider is purely additive. No existing test, agent, or doc has to change.

- v1.0.0 behaviour: unset `RTIA_LLM_PROVIDER` → Google. **Unchanged.**
- v1.0.0 behaviour: `RTIA_LLM_PROVIDER=ollama` → Ollama. **Unchanged.**
- New v1.1.0 behaviour: `RTIA_LLM_PROVIDER=fake` → FakeChatModel. **New.**
- New v1.1.0 behaviour: invalid value (e.g. `xyz`) → clear `ValueError` at startup naming the valid set. **New** (today an unknown value silently falls through to Google).

The fail-fast on invalid values is a small behaviour change but defensible: an invalid value today is almost certainly a typo that silently swallows a developer's intent. Per `feedback_verify_facts`, fail loud.

## Consequences

**Positive:**
- Playwright E2E suite ([Epic 7](https://github.com/augustineuzokwe/rtia/issues/292)) becomes feasible at $0 / PR.
- Unit-test ergonomics improve dramatically (one env var instead of five `patch.object` calls).
- Manual UI demos can run forever without burning quota — useful for the public-hosting work parked under Epic 9.

**Negative:**
- A third code path to maintain in every agent's `_build_llm()`. Mitigated by the existing `use_ollama()` pattern — the new branch is one extra `if`.
- Fixtures and Pydantic schemas can drift. Mitigated by US-35's validation pytest.
- Newcomers may not realise `RTIA_FAKE_SCENARIO` exists when reading test output. Mitigated by including it in the new ADR + `docs/USAGE.md`.

**Neutral:**
- The eval gate (Gemini-backed) is unaffected. Quality regression measurement still happens on real LLM output.
- The Ollama probe surface ([ADR-0012 §"out of scope but in flight"](adr-0012-v1-single-user-local.md), `docs/ollama-probe-2026-05-26.md`) is unaffected.

## References

- [ADR-0006: Provider switch](adr-0006-provider-switch.md) — original Google ↔ Ollama seam this ADR extends.
- [ADR-0007: Gemini 3.5 Flash switch](adr-0007-gemini-3-5-flash-switch.md) — current default model.
- [ADR-0012: v1 = single-user local](adr-0012-v1-single-user-local.md) — establishes that adding deterministic test infra doesn't change the adopter profile.
- `feedback_mock_class_per_module` — the per-import-site mock-patching pain this provider partially replaces.
- v1.1.0 plan: `~/.claude/plans/prepare-prepare-the-v2-deep-liskov.md`.
- Implementation epic: [#293](https://github.com/augustineuzokwe/rtia/issues/293).
