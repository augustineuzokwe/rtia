"""LangGraph orchestration for the RTIA pipeline.

Defines the shared `PipelineState` and the compiled pipeline graph that
chains agents together. Currently wires the Analyst, the PO checkpoint,
and the User Story Writer; subsequent agents (AC Generator, Test Case,
Reviewer) attach as additional nodes without rewiring the existing
structure.

Checkpointing uses SQLite by default so paused human-in-the-loop threads
survive process restarts (see `docs/adr-0002-durable-checkpointer.md`).
Test code injects an in-memory SQLite saver via `build_pipeline(checkpointer=...)`.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.final_artifact import AcceptanceCriterion, FinalUserStory, TestCase
from agents.requirements_analyst import (
    Ambiguity,
    AnalystOutput,
    ImpliedStory,
    analyze_requirement,
)
from agents.user_story_writer import UserStory, write_user_story

DEFAULT_CHECKPOINT_DB = "~/.rtia/state.db"
"""Default SQLite path for the checkpointer; overridable via RTIA_CHECKPOINT_DB."""

PIPELINE_STATE_VERSION = 1
"""Schema version marker for PipelineState.

Bump when fields are removed or renamed (additions are backward-compatible
because `total=False` allows missing keys). A bump means existing SQLite
state files may need migration — document the migration in an ADR.
"""

# Pydantic types we serialize into checkpointed state. LangGraph's msgpack
# serializer refuses to deserialize unknown types in newer releases, so we
# explicitly allowlist these — fixes the warning that has surfaced on every
# demo run since the Analyst started returning Pydantic models.
_CHECKPOINT_ALLOWLIST: list[tuple[str, ...]] = [
    ("agents.requirements_analyst", "AnalystOutput"),
    ("agents.requirements_analyst", "Ambiguity"),
    ("agents.requirements_analyst", "ImpliedStory"),
    ("agents.user_story_writer", "UserStory"),
    ("agents.final_artifact", "FinalUserStory"),
    ("agents.final_artifact", "AcceptanceCriterion"),
    ("agents.final_artifact", "TestCase"),
]


class PipelineState(TypedDict, total=False):
    """Shared state flowing through the RTIA pipeline.

    Each node reads what it needs from prior fields and writes its own
    output slot. `total=False` means fields are populated incrementally as
    the graph runs — the initial invoke only needs `requirement_text`.

    Schema version: see PIPELINE_STATE_VERSION. Field additions are safe;
    removals or renames require a version bump + migration plan.
    """

    requirement_text: str
    analyst_output: AnalystOutput
    po_answers: dict[str, str]
    user_story: UserStory
    final_artifact: FinalUserStory


def analyst_node(state: PipelineState) -> dict:
    """Run the Requirements Analyst on `state["requirement_text"]`."""
    result = analyze_requirement(state["requirement_text"])
    return {"analyst_output": result}


def po_checkpoint_node(state: PipelineState) -> dict:
    """Pause the graph for PO input only when critical ambiguities exist.

    If the Analyst flagged any ambiguity as "critical", the graph pauses
    via LangGraph's `interrupt()` and waits for the caller to resume with
    a dict mapping each critical question to its answer. If all ambiguities
    are "normal", the checkpoint passes through immediately — the Story
    Writer will treat normal ambiguities as story assumptions.

    Requires the compiled graph to have a checkpointer (see build_pipeline).
    """
    critical = [a for a in state["analyst_output"].ambiguities if a.severity == "critical"]
    if not critical:
        return {"po_answers": {}}

    answers = interrupt({"critical_ambiguities": [a.question for a in critical]})
    return {"po_answers": answers}


def story_writer_node(state: PipelineState) -> dict:
    """Run the User Story Writer on the Analyst's output + PO answers."""
    story = write_user_story(state["analyst_output"], state.get("po_answers", {}))
    return {"user_story": story}


def composer_node(state: PipelineState) -> dict:
    """Assemble the final artifact from current pipeline state.

    Today this is a pure transformation (no LLM call): description +
    objective + assumptions come from the Story Writer; AC and Test
    Case sections stay empty (their authoring agents land in Phase 8/9).
    This node is the explicit sink of the pipeline; downstream agents
    will WRITE into `final_artifact.acceptance_criteria` /
    `.test_cases` in later phases, with this composer remaining the
    final-state assembler.
    """
    story = state["user_story"]
    artifact = FinalUserStory(
        description=story.description,
        objective=story.objective,
        acceptance_criteria=[],
        test_cases=[],
        assumptions=list(story.assumptions),
        metadata={},
    )
    return {"final_artifact": artifact}


def _allowlisted_serde() -> JsonPlusSerializer:
    """Build the serializer with our Pydantic types pre-registered.

    Passing this serde directly to `SqliteSaver(conn, serde=...)` is the
    fix that survives cross-process reads. `SqliteSaver.with_allowlist()`
    only configures the local saver instance — when a fresh process
    re-opens the same DB with a fresh saver, the deserializer falls back
    to its default (warning-on-unknown) behavior. Direct serde
    construction makes the allowlist part of every read and write.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_ALLOWLIST)


def _default_sqlite_checkpointer() -> BaseCheckpointSaver:
    """Build the production-default checkpointer: SQLite at the configured path.

    The DB file lives at `RTIA_CHECKPOINT_DB` if set, otherwise
    `~/.rtia/state.db`. Parent directory is auto-created.

    Idempotency: a pipeline invoked with the same `thread_id` resumes
    existing state; a new `thread_id` is a fresh run. This is the
    LangGraph default and is documented in ADR-0002.

    `check_same_thread=False` lets the saver be used from worker threads
    (LangGraph spins these up internally). The connection's lifetime
    is tied to the saver instance, which is tied to the compiled
    pipeline returned by `build_pipeline()`.
    """
    db_path_str = os.environ.get("RTIA_CHECKPOINT_DB") or DEFAULT_CHECKPOINT_DB
    db_path = Path(db_path_str).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    return SqliteSaver(conn, serde=_allowlisted_serde())


def build_pipeline(checkpointer: BaseCheckpointSaver | None = None):
    """Build and compile the RTIA pipeline graph.

    `checkpointer=None` (default) uses the durable SQLite saver — paused
    human-in-the-loop threads survive process restarts. Pass an explicit
    checkpointer to override (tests inject an in-memory SQLite saver; a
    future production UI may pass a Postgres saver).

    Callers build their own pipeline instance (no module-level singleton)
    so production and tests go through the same construction path.
    """
    if checkpointer is None:
        checkpointer = _default_sqlite_checkpointer()

    builder = StateGraph(PipelineState)
    builder.add_node("analyst", analyst_node)
    builder.add_node("po_checkpoint", po_checkpoint_node)
    builder.add_node("story_writer", story_writer_node)
    builder.add_node("composer", composer_node)
    builder.add_edge(START, "analyst")
    builder.add_edge("analyst", "po_checkpoint")
    builder.add_edge("po_checkpoint", "story_writer")
    builder.add_edge("story_writer", "composer")
    builder.add_edge("composer", END)
    return builder.compile(checkpointer=checkpointer)


# Re-export the types we allowlist so callers (tests, future eval harness)
# can compare against the contract without reaching into agent modules.
__all__ = [
    "AcceptanceCriterion",
    "Ambiguity",
    "AnalystOutput",
    "FinalUserStory",
    "ImpliedStory",
    "PIPELINE_STATE_VERSION",
    "PipelineState",
    "TestCase",
    "UserStory",
    "_CHECKPOINT_ALLOWLIST",
    "_allowlisted_serde",
    "build_pipeline",
]
