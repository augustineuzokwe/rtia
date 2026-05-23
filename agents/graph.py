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
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents._llm_errors import LLMPipelineError, PipelineStepError, wrap_step_exception
from agents._secret_scan import raise_if_secrets_found
from agents.ac_generator import AcGeneratorOutput, generate_acceptance_criteria
from agents.final_artifact import AcceptanceCriterion, FinalUserStory, TestCase
from agents.requirements_analyst import (
    Ambiguity,
    AnalystOutput,
    ImpliedStory,
    analyze_requirement,
)
from agents.reviewer import ReviewReport, review_artifact
from agents.test_case_writer import TestCaseWriterOutput, write_test_cases
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
    ("agents.requirements_analyst", "SuspiciousInput"),
    ("agents.user_story_writer", "UserStory"),
    ("agents.ac_generator", "AcGeneratorOutput"),
    ("agents.test_case_writer", "TestCaseWriterOutput"),
    ("agents.final_artifact", "FinalUserStory"),
    ("agents.final_artifact", "AcceptanceCriterion"),
    ("agents.final_artifact", "TestCase"),
    ("agents.reviewer", "ReviewReport"),
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
    acceptance_criteria: list[AcceptanceCriterion]
    test_cases: list[TestCase]
    final_artifact: FinalUserStory
    review_report: ReviewReport


def _wrap_node_exceptions(agent_name: str, fn):
    """Convert unexpected node failures into structured ``PipelineStepError``.

    Phase 13.4. ``LLMPipelineError`` already carries its own structured
    detail and re-raises unchanged. Anything else (Pydantic validation,
    JSON parse, programmer errors) is wrapped with the node's
    ``agent_name`` pinned so the demo can route it through the same
    stub-artifact builder as LLM failures. ``KeyboardInterrupt`` and
    ``SystemExit`` propagate untouched — we are NOT a catch-everything
    shield, only a "surface step failures legibly" boundary.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(state: PipelineState) -> dict:
        try:
            return fn(state)
        except (LLMPipelineError, GraphBubbleUp, KeyboardInterrupt, SystemExit):
            # GraphBubbleUp covers LangGraph's internal control-flow
            # exceptions (GraphInterrupt from `interrupt()`, GraphDrained,
            # etc.) — those are NOT failures and must propagate to the
            # runtime untouched.
            raise
        except Exception as exc:
            raise wrap_step_exception(agent_name, exc) from exc

    return wrapper


def analyst_node(state: PipelineState) -> dict:
    """Run the Requirements Analyst on `state["requirement_text"]`.

    Phase 12.3: defensively scan the input for high-confidence secret
    patterns BEFORE the LLM call. If a match is found, raise
    ``SecretInInputError`` — no Gemini call, no LangSmith trace, no
    downstream node runs. This duplicates the demo script's pre-graph
    scan as belt-and-braces: any caller that bypasses the demo (tests,
    alternate scripts, future API) still gets the same protection.
    """
    raise_if_secrets_found(state["requirement_text"])
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


def story_review_checkpoint_node(state: PipelineState) -> dict:
    """Pause for PO review of the rendered story before the Composer finalizes.

    Always pauses (unlike the PO Checkpoint which pauses only on critical
    ambiguities). Renders a preview FinalUserStory via the same
    `as_markdown()` the demo and downstream consumers see, then waits
    for the caller to resume with one of:

        {"accepted": True}                    → pass through; use Story
                                                Writer's output as-is.

        {"accepted": False,
         "description": "...",                → direct field-level overrides;
         "objective": "..."}                    user_story is mutated in
                                                state before the Composer
                                                runs.

    Direct overrides rather than re-running the Story Writer is the
    intentional anti-iteration design (see `docs/adr-0005-story-review-
    overrides.md`). The PO knows what they want; they write it. The
    pipeline doesn't loop.

    Distinguishable from the PO Checkpoint at the demo / API layer by
    the interrupt payload keys: PO Checkpoint emits
    `{"critical_ambiguities": [...]}`; this node emits
    `{"rendered_artifact": ..., "description": ..., "objective": ...,
    "assumptions": [...]}`.
    """
    story = state["user_story"]
    preview = FinalUserStory(
        description=story.description,
        objective=story.objective,
        assumptions=list(story.assumptions),
    )
    response = interrupt(
        {
            "rendered_artifact": preview.as_markdown(),
            "description": story.description,
            "objective": story.objective,
            "assumptions": list(story.assumptions),
        }
    )
    if not isinstance(response, dict) or response.get("accepted") is True:
        # Accept (default): no state change; Composer uses the Story
        # Writer's output unchanged.
        return {}

    new_description = response.get("description") or story.description
    new_objective = response.get("objective") or story.objective
    revised = UserStory(
        description=new_description,
        objective=new_objective,
        assumptions=story.assumptions,
    )
    return {"user_story": revised}


def ac_generator_node(state: PipelineState) -> dict:
    """Run the AC Generator on the (possibly PO-revised) Story Writer output.

    Sits after the Story Review Checkpoint so any direct PO overrides on
    description / objective are already baked into `state["user_story"]`
    before the AC Generator sees them. The Generator never re-reads the
    original requirement — `state["analyst_output"]` is its sole source
    of truth for the requirement's actor set and intent.
    """
    result = generate_acceptance_criteria(
        state["user_story"],
        state["analyst_output"],
        state.get("po_answers", {}),
    )
    return {"acceptance_criteria": result.criteria}


def test_case_writer_node(state: PipelineState) -> dict:
    """Run the Test Case Writer on the user story + generated ACs.

    Sits after the AC Generator so the ACs are already pinned down before
    test cases derive from them. The Writer never re-reads the original
    requirement or the Analyst output — the story and ACs are its sole
    sources of truth.
    """
    result = write_test_cases(
        state["user_story"],
        list(state.get("acceptance_criteria", [])),
    )
    return {"test_cases": result.cases}


def composer_node(state: PipelineState) -> dict:
    """Assemble the final artifact from current pipeline state.

    Pure transformation (no LLM call): description + objective +
    assumptions come from the Story Writer; acceptance criteria come
    from the AC Generator; test cases come from the Test Case Writer.
    This node is the explicit sink of the pipeline; upstream agents
    WRITE into their respective state slots and the composer assembles
    whatever's currently there.
    """
    story = state["user_story"]
    artifact = FinalUserStory(
        description=story.description,
        objective=story.objective,
        acceptance_criteria=list(state.get("acceptance_criteria", [])),
        test_cases=list(state.get("test_cases", [])),
        assumptions=list(story.assumptions),
        metadata={},
    )
    return {"final_artifact": artifact}


def deferred_implied_stories(state: PipelineState) -> list[ImpliedStory]:
    """Pick out the implied stories the PO deferred at the PO checkpoint.

    When the Analyst flags a multi-story requirement, the PO checkpoint
    asks "which single story should this issue cover?" and stores the
    PO's answer in ``state["po_answers"]``. Everything in
    ``analyst_output.implied_stories`` whose title does NOT appear in
    any PO answer is treated as deferred — those are the behaviours the
    PO has indicated will become separate issues, and the Reviewer must
    NOT flag them as coverage gaps (LEARNINGS #31).

    Match is case-insensitive substring on the story title against the
    concatenated PO answers. This is intentionally loose — POs may
    answer "Slack notifications" when the story title is "Slack
    notifications for auto-quarantine changes", and we want that to
    count as picked, not deferred.

    Returns the full ``implied_stories`` list when the PO checkpoint
    never fired (no critical ambiguities) — that's the "all 4 stories
    are still in play, none picked" case. Caller (the Reviewer) treats
    it as story-level scoping context; an empty-list result means
    no story-level scoping happened at all (single-feature requirement).
    """
    analyst = state.get("analyst_output")
    if analyst is None or not analyst.implied_stories:
        return []
    po_answers = state.get("po_answers") or {}
    answers_blob = " | ".join(a.lower() for a in po_answers.values() if a)
    if not answers_blob:
        # PO checkpoint never fired (or fired with empty answers).
        # Conservative: every implied story is in play; nothing is
        # deferred. Returning [] keeps the Reviewer's old behaviour
        # for non-multi-story runs untouched.
        return []
    return [s for s in analyst.implied_stories if s.title.lower() not in answers_blob]


def reviewer_node(state: PipelineState) -> dict:
    """Run the Reviewer on the assembled artifact.

    Reads the original requirement text and the FinalUserStory produced by
    the Composer. Writes the full ReviewReport into state so evals and the
    demo can inspect individual fields. Also appends a one-line quality
    summary to final_artifact.metadata so the rendered markdown always
    surfaces review notes alongside the artifact.

    Phase 15.1 — scope-aware. Passes any implied stories the PO deferred
    at the PO checkpoint into ``review_artifact`` so the Reviewer stops
    flagging deferred behaviours as coverage gaps (LEARNINGS #31).
    """
    artifact = state["final_artifact"]
    deferred = deferred_implied_stories(state)
    report = review_artifact(state["requirement_text"], artifact, deferred_stories=deferred)

    summary_parts = []
    if report.coverage_gaps:
        summary_parts.append(f"gaps: {'; '.join(report.coverage_gaps)}")
    if report.weak_acs:
        summary_parts.append(f"weak ACs: {'; '.join(report.weak_acs)}")
    if report.untestable_criteria:
        summary_parts.append(f"untestable: {'; '.join(report.untestable_criteria)}")
    if report.recommendations:
        summary_parts.append(f"recommendations: {'; '.join(report.recommendations)}")
    tag = f"[{report.overall_quality}]"
    summary = f"{tag} " + " | ".join(summary_parts) if summary_parts else tag

    updated_artifact = artifact.model_copy(
        update={"metadata": {**artifact.metadata, "review_summary": summary}}
    )
    return {"final_artifact": updated_artifact, "review_report": report}


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
    # Each node is wrapped so unexpected exceptions (Pydantic validation,
    # JSON parse, programmer errors) surface as PipelineStepError with
    # agent attribution — see Phase 13.4. The checkpoint nodes are also
    # wrapped because user-supplied resume payloads can violate shape
    # assumptions; attributing those failures to the checkpoint, not the
    # next agent, keeps debugging honest.
    builder.add_node("analyst", _wrap_node_exceptions("requirements_analyst", analyst_node))
    builder.add_node("po_checkpoint", _wrap_node_exceptions("po_checkpoint", po_checkpoint_node))
    builder.add_node("story_writer", _wrap_node_exceptions("user_story_writer", story_writer_node))
    builder.add_node(
        "story_review_checkpoint",
        _wrap_node_exceptions("story_review_checkpoint", story_review_checkpoint_node),
    )
    builder.add_node("ac_generator", _wrap_node_exceptions("ac_generator", ac_generator_node))
    builder.add_node(
        "test_case_writer", _wrap_node_exceptions("test_case_writer", test_case_writer_node)
    )
    builder.add_node("composer", _wrap_node_exceptions("composer", composer_node))
    builder.add_node("reviewer", _wrap_node_exceptions("reviewer", reviewer_node))
    builder.add_edge(START, "analyst")
    builder.add_edge("analyst", "po_checkpoint")
    builder.add_edge("po_checkpoint", "story_writer")
    builder.add_edge("story_writer", "story_review_checkpoint")
    builder.add_edge("story_review_checkpoint", "ac_generator")
    builder.add_edge("ac_generator", "test_case_writer")
    builder.add_edge("test_case_writer", "composer")
    builder.add_edge("composer", "reviewer")
    builder.add_edge("reviewer", END)
    return builder.compile(checkpointer=checkpointer)


def build_stub_artifact_from_error(error: PipelineStepError) -> FinalUserStory:
    """Produce a ``FinalUserStory`` describing a pipeline-step failure.

    Accepts any ``PipelineStepError`` — including its narrower
    ``LLMPipelineError`` subclass (Phase 12.5: Gemini retry budget
    exhausted) and the broader Phase 13.4 wrapping of unexpected node
    failures (Pydantic validation, JSON parse, programmer errors). The
    artifact has empty sections (the agent that would have filled them
    never finished) and carries the structured failure detail as JSON
    in ``metadata['error']`` plus a human-readable summary in
    ``metadata['review_summary']`` so the rendered markdown surfaces
    the failure inline.

    The whole point of this helper is to keep the failure path
    *symmetrical* with the success path — both produce a FinalUserStory
    that can be rendered, persisted, or returned over an API. The
    caller never has to choose between "got an artifact" and "got an
    exception"; it always gets an artifact and inspects metadata to
    learn whether the pipeline ran successfully. See
    ``docs/adr-0009-llm-fallback.md`` for the full policy.
    """
    detail = error.detail
    if detail.http_status is None and detail.retries_attempted == 0:
        # Non-LLM step failure (Phase 13.4). Skip the LLM-specific
        # status/retries suffix that would just say "None" / "0".
        summary = (
            f"[ERROR] Step failure in '{detail.agent}' "
            f"(class={detail.error_class}). "
            "Pipeline aborted — see metadata.error for the structured detail."
        )
    else:
        summary = (
            f"[ERROR] LLM failure in '{detail.agent}' "
            f"(class={detail.error_class}, "
            f"status={detail.http_status}, "
            f"retries={detail.retries_attempted}). "
            "Pipeline aborted — see metadata.error for the structured detail."
        )
    return FinalUserStory(
        description="_Pipeline aborted before this section was written. See metadata.error._",
        objective="_Pipeline aborted before this section was written. See metadata.error._",
        acceptance_criteria=[],
        test_cases=[],
        assumptions=[],
        metadata={
            "error": error.detail.to_json(),
            "review_summary": summary,
        },
    )


# Re-export the types we allowlist so callers (tests, future eval harness)
# can compare against the contract without reaching into agent modules.
__all__ = [
    "deferred_implied_stories",
    "AcGeneratorOutput",
    "AcceptanceCriterion",
    "Ambiguity",
    "AnalystOutput",
    "FinalUserStory",
    "ImpliedStory",
    "LLMPipelineError",
    "PIPELINE_STATE_VERSION",
    "PipelineState",
    "PipelineStepError",
    "ReviewReport",
    "TestCase",
    "TestCaseWriterOutput",
    "UserStory",
    "_CHECKPOINT_ALLOWLIST",
    "_allowlisted_serde",
    "build_pipeline",
    "build_stub_artifact_from_error",
]
