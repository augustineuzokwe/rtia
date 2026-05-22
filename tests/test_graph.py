"""Tests for the RTIA pipeline graph.

Mocks the underlying LLM calls so tests run offline and deterministically.
Covers graph wiring: happy-path flow-through through the Story Writer,
pause at critical ambiguities, and resume with PO answers (also routing
through the Story Writer).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents.graph import (
    _CHECKPOINT_ALLOWLIST,
    PIPELINE_STATE_VERSION,
    PipelineState,
    _allowlisted_serde,
    build_pipeline,
)


def _test_checkpointer() -> SqliteSaver:
    """Build an in-memory SQLite checkpointer for unit tests.

    `check_same_thread=False` lets LangGraph use the saver from its worker
    threads. The allowlisted serde matches production so tests exercise
    the same serialization path that the demo runs in.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return SqliteSaver(conn, serde=_allowlisted_serde())


def _fake_analyst_response(ambiguities: list[dict]) -> dict:
    """Build a minimal Analyst response payload with the given ambiguities."""
    return {
        "intent": "Goal X",
        "actors": ["User"],
        "ambiguities": ambiguities,
    }


FAKE_STORY_RESPONSE = {
    "description": "As a user, I want to do thing X.",
    "objective": "Outcome Y is achieved.",
    "assumptions": [],
}

FAKE_AC_RESPONSE = {
    "criteria": [
        {"given": "the user is logged in", "when": "they do X", "then": "outcome Y appears"}
    ]
}

FAKE_TEST_CASE_RESPONSE = {
    "cases": [
        {
            "scenario": "Happy path — user does X",
            "type": "happy_path",
            "steps": ["Log in as a user.", "Do thing X."],
            "expected": "Outcome Y is observed.",
        },
        {
            "scenario": "Unauthenticated user blocked",
            "type": "negative",
            "steps": ["Open the page without logging in."],
            "expected": "The user is redirected to login before doing X.",
        },
    ]
}

FAKE_REVIEW_RESPONSE = {
    "coverage_gaps": [],
    "weak_acs": [],
    "untestable_criteria": [],
    "recommendations": [],
    "overall_quality": "strong",
}


def _llm_factory(payload: dict):
    """Build a fake ChatGoogleGenerativeAI class whose instances return `payload`."""

    def _factory(**_kwargs):
        instance = MagicMock()
        instance.invoke.return_value = AIMessage(content=json.dumps(payload))
        return instance

    return _factory


def _mock_pipeline_llms(
    analyst_payload: dict,
    story_payload: dict = FAKE_STORY_RESPONSE,
    ac_payload: dict = FAKE_AC_RESPONSE,
    test_case_payload: dict = FAKE_TEST_CASE_RESPONSE,
    review_payload: dict = FAKE_REVIEW_RESPONSE,
):
    """Patch each agent's `ChatGoogleGenerativeAI` symbol with its own fake.

    The LangChain wrapper is the same class object in every agent module, so
    patching `.invoke` on the class would bleed across agents. Patching the
    symbol at each import site keeps the mocks independent (see feedback
    memory `feedback_mock_class_per_module`).
    """
    stack = ExitStack()
    stack.enter_context(
        patch(
            "agents.requirements_analyst.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(analyst_payload),
        )
    )
    stack.enter_context(
        patch(
            "agents.user_story_writer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(story_payload),
        )
    )
    stack.enter_context(
        patch("agents.ac_generator.ChatGoogleGenerativeAI", side_effect=_llm_factory(ac_payload))
    )
    stack.enter_context(
        patch(
            "agents.test_case_writer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(test_case_payload),
        )
    )
    stack.enter_context(
        patch(
            "agents.reviewer.ChatGoogleGenerativeAI",
            side_effect=_llm_factory(review_payload),
        )
    )
    return stack


def test_pipeline_flows_through_when_no_critical_ambiguities():
    """No critical ambiguities → PO checkpoint flows through. Story Review still pauses;
    resuming with accept lands the full pipeline."""
    analyst = _fake_analyst_response([{"question": "Style preference?", "severity": "normal"}])

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-flow-through"}}
        first = pipeline.invoke({"requirement_text": "some requirement"}, config=config)
        # The Story Review Checkpoint always pauses for explicit accept.
        assert "rendered_artifact" in first["__interrupt__"][0].value
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert "__interrupt__" not in result
    assert result["analyst_output"].intent == "Goal X"
    assert result["po_answers"] == {}
    assert result["user_story"].description.startswith("As a user, I want")
    assert "thing X" in result["user_story"].description
    # Composer runs after Story Review accept — final_artifact populated
    assert "final_artifact" in result
    assert result["final_artifact"].description == result["user_story"].description
    # AC Generator (Phase 8) populates this slot from FAKE_AC_RESPONSE.
    assert len(result["final_artifact"].acceptance_criteria) == 1
    assert result["final_artifact"].acceptance_criteria[0].then == "outcome Y appears"
    # Test Case Writer (Phase 9) populates this slot from FAKE_TEST_CASE_RESPONSE.
    assert len(result["final_artifact"].test_cases) == 2
    types = {tc.type for tc in result["final_artifact"].test_cases}
    assert {"happy_path", "negative"} <= types
    # Reviewer (Phase 10) populates this slot from FAKE_REVIEW_RESPONSE.
    assert "review_report" in result
    assert result["review_report"].overall_quality == "strong"
    assert result["review_report"].coverage_gaps == []
    # Reviewer appends a summary to the artifact's metadata.
    assert "review_summary" in result["final_artifact"].metadata
    assert "[strong]" in result["final_artifact"].metadata["review_summary"]


def test_reviewer_summary_includes_all_report_categories():
    """All four ReviewReport list categories must surface in metadata['review_summary'].

    Regression guard — an earlier draft skipped untestable_criteria from the
    summary string, which would silently hide a real review finding from
    anyone reading only the rendered markdown.
    """
    analyst = _fake_analyst_response([])
    review_payload = {
        "coverage_gaps": ["No AC covers feature Z."],
        "weak_acs": ["AC1 — vague then-clause"],
        "untestable_criteria": ["AC2 — subjective outcome"],
        "recommendations": ["Add concrete AC for Z."],
        "overall_quality": "needs_work",
    }

    with _mock_pipeline_llms(analyst, review_payload=review_payload):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-review-summary"}}
        pipeline.invoke({"requirement_text": "some requirement"}, config=config)
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    summary = result["final_artifact"].metadata["review_summary"]
    assert "[needs_work]" in summary
    assert "gaps:" in summary
    assert "weak ACs:" in summary
    assert "untestable:" in summary
    assert "recommendations:" in summary


def test_pipeline_pauses_when_critical_ambiguity_present():
    """A critical ambiguity should pause the graph before the Story Writer."""
    analyst = _fake_analyst_response(
        [
            {"question": "What role is the actor?", "severity": "critical"},
            {"question": "Display style?", "severity": "normal"},
        ]
    )

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-pause"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" in result
    interrupt_value = result["__interrupt__"][0].value
    assert interrupt_value["critical_ambiguities"] == ["What role is the actor?"]
    # Story Writer must not have run yet.
    assert "user_story" not in result


def test_pipeline_resumes_into_story_writer_with_po_answers():
    """Resuming the paused graph should populate po_answers and produce a story."""
    analyst = _fake_analyst_response(
        [{"question": "What role is the actor?", "severity": "critical"}]
    )

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-resume"}}
        pipeline.invoke({"requirement_text": "some requirement"}, config=config)

        # First resume: provide PO answers → pipeline runs Story Writer
        # → pauses at Story Review Checkpoint.
        answers = {"What role is the actor?": "QA Lead"}
        intermediate = pipeline.invoke(Command(resume=answers), config=config)
        assert "rendered_artifact" in intermediate["__interrupt__"][0].value

        # Second resume: accept the rendered story → Composer runs → END.
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert "__interrupt__" not in result
    assert result["po_answers"] == answers
    assert result["user_story"].description.startswith("As a user, I want")
    # Composer runs after Story Review accept
    assert "final_artifact" in result
    assert result["final_artifact"].description == result["user_story"].description


# ---------------------------------------------------------------------------
# Schema stability + checkpointer-injection contract
# ---------------------------------------------------------------------------


def test_pipeline_state_v1_schema_is_stable():
    """If a v1 field is removed or renamed, this test fails LOUDLY.

    Adding fields is safe (PipelineState is total=False). Removing or
    renaming requires bumping PIPELINE_STATE_VERSION and writing a
    migration ADR — silent state-shape drift after PR #40's checkpoint
    landed would corrupt every paused thread on disk.
    """
    expected_v1_fields = {
        "requirement_text",
        "analyst_output",
        "po_answers",
        "user_story",
        "test_cases",
        "final_artifact",
    }
    actual_fields = set(PipelineState.__annotations__.keys())
    missing = expected_v1_fields - actual_fields
    assert not missing, (
        f"Pipeline state v1 fields {missing} were removed. "
        f"Either restore them or bump PIPELINE_STATE_VERSION "
        f"(currently {PIPELINE_STATE_VERSION}) with a migration ADR."
    )


def test_checkpoint_allowlist_covers_all_pydantic_state_types():
    """The msgpack allowlist must include every Pydantic type we put in state.

    A missing allowlist entry resurfaces the noisy
    'Deserializing unregistered type ...' warning that PR #59 fixed.
    This test catches it the moment a new Pydantic type slips into state.
    """
    allowed = {(mod, cls) for mod, cls in _CHECKPOINT_ALLOWLIST}
    required = {
        ("agents.requirements_analyst", "AnalystOutput"),
        ("agents.requirements_analyst", "Ambiguity"),
        ("agents.requirements_analyst", "ImpliedStory"),
        ("agents.user_story_writer", "UserStory"),
        ("agents.test_case_writer", "TestCaseWriterOutput"),
        ("agents.final_artifact", "FinalUserStory"),
        ("agents.final_artifact", "AcceptanceCriterion"),
        ("agents.final_artifact", "TestCase"),
    }
    missing = required - allowed
    assert not missing, f"Pydantic types not in checkpoint allowlist: {missing}"


def test_build_pipeline_accepts_injected_checkpointer():
    """Tests + future API/UI use cases must be able to inject a checkpointer."""
    pipeline = build_pipeline(checkpointer=_test_checkpointer())
    # Smoke check: the compiled pipeline has nodes wired.
    assert pipeline is not None


# ---------------------------------------------------------------------------
# Story Review Checkpoint (Phase 4)
# ---------------------------------------------------------------------------


def test_pipeline_pauses_at_story_review_checkpoint():
    """After Story Writer, the graph pauses with a rendered-artifact preview.

    Distinguishable from the PO Checkpoint by interrupt payload keys —
    PO emits `critical_ambiguities`; Story Review emits
    `rendered_artifact` + `description` + `objective`.
    """
    analyst = _fake_analyst_response([])  # no critical ambiguities → goes straight through PO
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-pause"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert "rendered_artifact" in payload
    assert "description" in payload and "objective" in payload
    # PO Checkpoint should NOT have been the one that fired here.
    assert "critical_ambiguities" not in payload
    # Composer must not have run yet.
    assert "final_artifact" not in result


def test_story_review_accept_passes_through():
    """Resuming with `{'accepted': True}` uses Story Writer's output unchanged."""
    analyst = _fake_analyst_response([])
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-accept"}}
        pipeline.invoke({"requirement_text": "x"}, config=config)
        result = pipeline.invoke(Command(resume={"accepted": True}), config=config)

    assert "__interrupt__" not in result
    artifact = result["final_artifact"]
    # Story Writer's mock produced "As a user, I want to do thing X." —
    # accept path preserves it unchanged.
    assert artifact.description == "As a user, I want to do thing X."
    assert artifact.objective == "Outcome Y is achieved."


def test_story_review_override_replaces_description_and_objective():
    """Resuming with overrides replaces description/objective in the final artifact."""
    analyst = _fake_analyst_response([])
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-override"}}
        pipeline.invoke({"requirement_text": "x"}, config=config)
        result = pipeline.invoke(
            Command(
                resume={
                    "accepted": False,
                    "description": "As a manager, I want overridden text.",
                    "objective": "Overridden value statement.",
                }
            ),
            config=config,
        )

    assert "__interrupt__" not in result
    artifact = result["final_artifact"]
    assert artifact.description == "As a manager, I want overridden text."
    assert artifact.objective == "Overridden value statement."


def test_story_review_override_partial_keeps_non_overridden_fields():
    """Empty / missing override fields fall back to the Story Writer's value.

    Pins the partial-override semantics: a PO who only wants to tweak
    the description shouldn't lose the writer's objective by omitting
    it.
    """
    analyst = _fake_analyst_response([])
    checkpointer = _test_checkpointer()

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-story-review-partial"}}
        pipeline.invoke({"requirement_text": "x"}, config=config)
        result = pipeline.invoke(
            Command(
                resume={
                    "accepted": False,
                    "description": "Only the description changed.",
                    # objective omitted → keeps writer's "Outcome Y is achieved."
                }
            ),
            config=config,
        )

    artifact = result["final_artifact"]
    assert artifact.description == "Only the description changed."
    assert artifact.objective == "Outcome Y is achieved."
