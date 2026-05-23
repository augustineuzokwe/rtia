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

PIPELINE_STATE_VERSION = 2
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

    Phase 15.4 added the fan-out fields (``selected_story_titles``,
    ``fan_out_stories``). Multi-story requirements bypass the deep nodes
    entirely — those fields are populated by ``fan_out_node`` and
    consumed by the API runner's DONE_FANOUT state and the
    ``/export-deferred`` endpoint. The deep-path fields
    (``user_story`` → ``final_artifact`` → ``review_report``) remain
    populated for single-story requirements.
    """

    requirement_text: str
    analyst_output: AnalystOutput
    po_answers: dict[str, str]
    user_story: UserStory
    acceptance_criteria: list[AcceptanceCriterion]
    test_cases: list[TestCase]
    final_artifact: FinalUserStory
    review_report: ReviewReport
    # Phase 15.4 — fan-out path.
    selected_story_titles: list[str]
    fan_out_stories: list[ImpliedStory]


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


def is_fanout_mode(state: PipelineState) -> bool:
    """Return True when the Analyst identified ≥ 2 implied stories.

    Phase 15.4. The branch criterion for the conditional edge at the PO
    checkpoint. Pure function of the Analyst's output — independent of
    the PO's eventual answer — so the same value is used both to shape
    the interrupt payload (CheckboxGroup vs text input) and to route
    after the checkpoint (fan-out vs deep).
    """
    analyst = state.get("analyst_output")
    return bool(analyst and analyst.implied_stories and len(analyst.implied_stories) >= 2)


def _looks_like_story_picker_question(question: str) -> bool:
    """Detect the Analyst's "which implied story?" critical question.

    Heuristic, not authoritative. The Analyst's prompt emits this
    question in a recognisable shape ("This requirement implies N
    independent stories: [..]. Which single story should this issue
    cover?"). In fan-out mode we drop this question from the PO panel's
    text-input list because the CheckboxGroup replaces it.

    If the heuristic misses (Analyst rewords the question), the worst
    case is a redundant text input next to the checkboxes — harmless,
    just untidy.
    """
    q = question.lower()
    return ("implies" in q and ("stories" in q or "single story" in q)) or (
        "which single story" in q
    )


def po_checkpoint_node(state: PipelineState) -> dict:
    """Pause the graph for PO input. Shape depends on fan-out vs deep mode.

    Phase 15.4. Two distinct interrupt payloads:

    - **Fan-out mode** (``implied_stories ≥ 2``): always pause, even when
      there are no non-story critical questions, so the PO confirms or
      unchecks the implied-story list. Payload carries the implied
      stories so the UI can render them as a CheckboxGroup. Resume
      value shape: ``{"selected_story_titles": [...], "answers": {...}}``.
    - **Deep mode** (``implied_stories ≤ 1``): unchanged from earlier
      phases — pause only when at least one critical ambiguity exists,
      resume value is the old ``dict[question, answer]``.

    The ``mode`` key in the interrupt payload tells the UI which control
    set to render. The ``_route_after_po`` conditional edge then
    dispatches to ``fan_out`` or ``story_writer`` based on
    :func:`is_fanout_mode`.

    Requires the compiled graph to have a checkpointer (see build_pipeline).
    """
    analyst = state["analyst_output"]
    critical = [a for a in analyst.ambiguities if a.severity == "critical"]
    fan_out = is_fanout_mode(state)

    if fan_out:
        non_story_critical = [
            a.question for a in critical if not _looks_like_story_picker_question(a.question)
        ]
        response = interrupt(
            {
                "mode": "fan_out",
                "implied_stories": [s.model_dump() for s in analyst.implied_stories],
                "critical_ambiguities": non_story_critical,
            }
        )
        if not isinstance(response, dict):
            response = {}
        selected = response.get("selected_story_titles")
        if not selected:
            # Q2 default: empty selection → fan out every identified story.
            selected = [s.title for s in analyst.implied_stories]
        return {
            "selected_story_titles": list(selected),
            "po_answers": dict(response.get("answers") or {}),
        }

    # Deep mode — original behaviour preserved.
    if not critical:
        return {"po_answers": {}}
    answers = interrupt({"mode": "deep", "critical_ambiguities": [a.question for a in critical]})
    return {"po_answers": answers}


def fan_out_node(state: PipelineState) -> dict:
    """Filter the Analyst's implied stories by the PO's checkbox selection.

    Phase 15.4. Pure Python — no LLM call. Skipped entirely in the
    deep-flow branch. The selected stories land in
    ``state["fan_out_stories"]`` and are surfaced by the API runner as
    a ``DONE_FANOUT`` payload + by the existing
    ``/pipeline/{id}/export-deferred`` endpoint when dispatching off
    that status.

    Empty ``selected_story_titles`` is treated as "fan out everything"
    (matches the Q2 default behaviour for an empty PO checkbox state).
    Match against ``ImpliedStory.title`` is case-insensitive after
    stripping whitespace — the UI sends back exact title strings, so
    no fuzzy match is needed here.
    """
    analyst = state["analyst_output"]
    selected_titles = state.get("selected_story_titles") or []
    if not selected_titles:
        return {"fan_out_stories": list(analyst.implied_stories)}
    titles_set = {t.lower().strip() for t in selected_titles if t and t.strip()}
    return {
        "fan_out_stories": [
            s for s in analyst.implied_stories if s.title.lower().strip() in titles_set
        ]
    }


def _route_after_po(state: PipelineState) -> str:
    """Conditional edge: where to send the graph after the PO checkpoint.

    ``is_fanout_mode`` is the sole criterion — the Analyst's output
    decides the mode, not the PO's selection. (A PO who unchecks all
    but one story still goes through fan-out; they just get a
    one-story fan-out. To get a deep artifact for that title, they
    re-run RTIA on the title alone.)
    """
    return "fan_out" if is_fanout_mode(state) else "deep"


def story_writer_node(state: PipelineState) -> dict:
    """Run the User Story Writer on the Analyst's output + PO answers + scope.

    Phase 15.4. When the Analyst produced multiple implied stories and the
    PO clearly picked one at the PO checkpoint, the Writer is told to
    narrow its description and objective to ONLY that picked story's
    scope. When no single story was picked (ambiguous PO answer, "all",
    no PO answer, or single-feature requirement), the Writer falls back
    to its original behaviour: write for the full intent. Symmetric with
    the scope-aware Reviewer (Phase 15.1).
    """
    picked = picked_implied_story(state)
    story = write_user_story(
        state["analyst_output"], state.get("po_answers", {}), picked_story=picked
    )
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


def picked_implied_story(state: PipelineState) -> ImpliedStory | None:
    """Identify the single implied story the PO picked at the PO checkpoint.

    Inverse of :func:`deferred_implied_stories`: returns the picked story
    instead of the leftovers. Returns ``None`` in any of the following
    "no clean pick" cases:

    - The Analyst didn't produce implied stories (single-feature
      requirement — there's nothing to scope down to).
    - The PO checkpoint never fired (no critical ambiguities flagged).
    - The PO answered but no story title matched (e.g. PO typed "all"
      or "split them up" — the answer doesn't name a specific story).
    - The PO's answer matches MORE than one story title (e.g. typed
      both "Story A and Story B" — ambiguous as a single pick; the
      caller should treat this as "no narrowing"). The deferred-export
      flow handles the "I want multiple as backlog issues" workflow.

    Match logic is the same bidirectional substring used by
    :func:`deferred_implied_stories` — title ⊂ answer OR answer ⊂
    title, case-insensitive — so the two helpers stay symmetric.

    Returning ``None`` (rather than e.g. a list of picks) is the
    intentional v1 contract: the Story Writer produces ONE story.
    Multi-story workflows belong to the deferred-export flow
    (Phase 15.3), which already creates lightweight follow-up issues
    for each unselected story.
    """
    analyst = state.get("analyst_output")
    if analyst is None or not analyst.implied_stories:
        return None
    # Exactly one implied story → unambiguously the picked one, no PO
    # answer needed. Phase 15.4 makes this the common deep-path case
    # (multi-implied requirements route to fan-out instead of reaching
    # the Story Writer at all).
    if len(analyst.implied_stories) == 1:
        return analyst.implied_stories[0]
    po_answers = state.get("po_answers") or {}
    answer_strings = [a.lower().strip() for a in po_answers.values() if a and a.strip()]
    if not answer_strings:
        return None
    matches = [s for s in analyst.implied_stories if _is_picked(s.title, answer_strings)]
    if len(matches) == 1:
        return matches[0]
    return None


def deferred_implied_stories(state: PipelineState) -> list[ImpliedStory]:
    """Pick out the implied stories the PO deferred at the PO checkpoint.

    When the Analyst flags a multi-story requirement, the PO checkpoint
    asks "which single story should this issue cover?" and stores the
    PO's answer in ``state["po_answers"]``. Everything in
    ``analyst_output.implied_stories`` whose title does NOT appear in
    any PO answer is treated as deferred — those are the behaviours the
    PO has indicated will become separate issues, and the Reviewer must
    NOT flag them as coverage gaps (LEARNINGS #31).

    Match is case-insensitive and **bidirectional substring**: a story is
    considered picked if any PO answer contains the title OR the title
    contains the answer. The bidirectionality covers the two realistic
    PO behaviours:

    - PO types a longer answer that includes the title verbatim
      ("Pick the dashboard story for now") — title ⊂ answer matches.
    - PO types a shorter variant of the title ("Slack notifications"
      when the full title is "Slack notifications for auto-quarantine
      changes") — answer ⊂ title matches.

    A naive single-direction substring match (which an earlier draft of
    this helper used) silently misclassifies one of those cases as
    deferred, undoing both 15.1 (Reviewer suppresses gap flags for the
    wrong story) and 15.3 (bulk export creates a follow-up for the
    picked story). The bidirectional form is the cheapest robust fix
    short of a real LLM-judge — fuzzy enough to be forgiving, strict
    enough that "dashboard" doesn't accidentally match "audit log".

    Returns the empty list when the PO checkpoint never fired (no
    critical ambiguities or empty answers) — that's the "all stories
    are still in play, none picked" case. Caller (the Reviewer) treats
    an empty list as story-level scoping context; an empty-list result
    means no story-level scoping happened at all (single-feature
    requirement).
    """
    analyst = state.get("analyst_output")
    if analyst is None or not analyst.implied_stories:
        return []
    po_answers = state.get("po_answers") or {}
    answer_strings = [a.lower().strip() for a in po_answers.values() if a and a.strip()]
    if not answer_strings:
        # PO checkpoint never fired (or fired with empty answers).
        # Conservative: every implied story is in play; nothing is
        # deferred. Returning [] keeps the Reviewer's old behaviour
        # for non-multi-story runs untouched.
        return []
    return [s for s in analyst.implied_stories if not _is_picked(s.title, answer_strings)]


def _is_picked(story_title: str, answers_lower: list[str]) -> bool:
    """Return True if any PO answer matches the story title in either direction.

    Both directions matter — see ``deferred_implied_stories`` for the
    rationale. ``answers_lower`` is the list of lower-cased / stripped
    PO answers; this helper does no normalisation of its own beyond
    lowercasing the title.
    """
    title = story_title.lower().strip()
    if not title:
        return False
    return any(title in ans or ans in title for ans in answers_lower)


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
    # Phase 15.4 — fan-out node. Pure Python, no wrapper LLM concerns.
    builder.add_node("fan_out", _wrap_node_exceptions("fan_out", fan_out_node))
    builder.add_edge(START, "analyst")
    builder.add_edge("analyst", "po_checkpoint")
    # Phase 15.4 — conditional edge after the PO checkpoint. Multi-story
    # requirements (≥ 2 implied stories) skip every downstream LLM node
    # and route straight to the fan-out node, which produces lightweight
    # backlog stubs instead of a deep artifact.
    builder.add_conditional_edges(
        "po_checkpoint",
        _route_after_po,
        {"fan_out": "fan_out", "deep": "story_writer"},
    )
    builder.add_edge("fan_out", END)
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
    "fan_out_node",
    "is_fanout_mode",
    "picked_implied_story",
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
