# ADR-0002: Durable checkpointer for paused human-in-the-loop threads

**Status:** Accepted (2026-05-19)
**Author:** augustineuzokwe
**Decision driver:** Phase 2 of the prod-readiness roadmap - `MemorySaver` (PR #40) loses every paused PO checkpoint thread on process restart. The "production note" in PR #40 explicitly called this out as deferred. Phase 2 honors it.

## Context

RTIA pauses at the PO Checkpoint when the Analyst flags critical ambiguities. The pause uses LangGraph's `interrupt()` primitive, which requires a checkpointer to persist state across pause/resume. The walking-skeleton choice in PR #40 was `MemorySaver` - fine for demos and tests, but a paused thread vanishes the moment Python exits.

A v1 production-runnable pipeline (the goal of this prod-ready plan) needs the paused state to survive:
- A PO answering a checkpoint hours after the Analyst ran.
- A laptop sleep / wake cycle.
- An unhandled exception in a downstream node - restart, resume from the last checkpoint.

## Decision

**Default checkpointer: `SqliteSaver` with a file-backed SQLite database.**

- **Path:** `~/.rtia/state.db` by default, overridable via the `RTIA_CHECKPOINT_DB` env var. Parent directory auto-created.
- **Allowlist:** Every Pydantic type that lands in `PipelineState` is registered via `SqliteSaver.with_allowlist([...])` - `AnalystOutput`, `Ambiguity`, `ImpliedStory`, `UserStory`. This fixes the "Deserializing unregistered type" warning that has surfaced on every demo run since PR #40 and which LangGraph has signaled will become a hard error in future releases.
- **Injection point:** `build_pipeline(checkpointer=None)` accepts an explicit checkpointer override. Tests inject an in-memory SQLite saver via `SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)).with_allowlist(...)`. Future production UIs (FastAPI/Streamlit) may pass a Postgres saver.

## Idempotency / replay semantics

The LangGraph default applies and is documented explicitly here so future readers don't have to read source:

- Invoking the pipeline with a **new `thread_id`** is a fresh run.
- Invoking with an **existing `thread_id`** resumes from that thread's most recent checkpoint. If the thread is paused at an `interrupt()`, providing a `Command(resume=...)` continues from there.
- Two simultaneous invocations of the same `thread_id` from different processes is **not safe** (SQLite's locking will not prevent inconsistent state). v1 is single-user - no concurrent-invocation guarantees.

## Why SQLite, not Postgres

- **v1 is local-dev only** per planning decision. SQLite is zero-config and ships with Python.
- **`langgraph-checkpoint-sqlite`** is an official LangGraph adapter - same maintenance burden as `langgraph-checkpoint-postgres`, but lighter to set up.
- **Post-v1 deployment** (Docker self-host or hosted SaaS) may swap to Postgres for multi-process safety. `build_pipeline()`'s injection seam makes the swap a single-file change.

## State schema versioning

`PIPELINE_STATE_VERSION = 1` is exported from `agents/graph.py`. A test in `tests/test_graph.py` asserts the v1 field set is stable:

```python
def test_pipeline_state_v1_schema_is_stable():
    expected_v1_fields = {"requirement_text", "analyst_output", "po_answers", "user_story"}
    actual_fields = set(PipelineState.__annotations__.keys())
    missing = expected_v1_fields - actual_fields
    assert not missing, ...
```

Adding fields is safe (`total=False`). Removing or renaming a field fails the test loudly - the change author must either restore the field or bump the version and write a migration ADR.

## Consequences

**Positive**
- Paused PO checkpoints survive process restarts.
- The msgpack warning disappears.
- Schema-stability test catches silent state-shape drift.
- Single injection seam supports both prod and tests.

**Negative / risks**
- New dependency: `langgraph-checkpoint-sqlite` plus transitive `aiosqlite` and `sqlite-vec`. Footprint is small but worth flagging.
- `~/.rtia/state.db` accumulates checkpoint data over time. No automatic pruning. Phase 13 may add a `gh-actions`-style retention sweep, or callers can `gh project item-add`-style call `saver.prune(...)`.
- SQLite is not safe for multi-process writes - v1 single-user is fine, post-v1 needs Postgres.
- The msgpack allowlist must be kept in sync with every new Pydantic type that lands in state. The new `test_checkpoint_allowlist_covers_all_pydantic_state_types` test catches the most common form of this drift but doesn't replace human review.

## Followups

- Phase 13 (operational hardening): retention/pruning policy for the state DB.
- Phase 14 (UI): the FastAPI surface will need to construct its own checkpointer with a chosen path; document the override pattern.
- Post-v1 deployment: swap to Postgres if multi-process or hosted-SaaS deployment lands.
