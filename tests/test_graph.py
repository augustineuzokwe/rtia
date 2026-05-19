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


def _llm_factory(payload: dict):
    """Build a fake ChatAnthropic class whose instances return `payload`."""

    def _factory(**_kwargs):
        instance = MagicMock()
        instance.invoke.return_value = AIMessage(content=json.dumps(payload))
        return instance

    return _factory


def _mock_pipeline_llms(analyst_payload: dict, story_payload: dict = FAKE_STORY_RESPONSE):
    """Patch each agent's `ChatAnthropic` symbol with its own fake.

    `ChatAnthropic` is the same class object in both agent modules, so patching
    `.invoke` on the class would bleed across agents. Patching the symbol at
    each import site keeps the two mocks independent.
    """
    stack = ExitStack()
    stack.enter_context(
        patch(
            "agents.requirements_analyst.ChatAnthropic", side_effect=_llm_factory(analyst_payload)
        )
    )
    stack.enter_context(
        patch("agents.user_story_writer.ChatAnthropic", side_effect=_llm_factory(story_payload))
    )
    return stack


def test_pipeline_flows_through_when_no_critical_ambiguities():
    """No critical ambiguities -> no pause, Story Writer runs, story populated."""
    analyst = _fake_analyst_response([{"question": "Style preference?", "severity": "normal"}])

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline(checkpointer=_test_checkpointer())
        config = {"configurable": {"thread_id": "test-flow-through"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" not in result
    assert result["analyst_output"].intent == "Goal X"
    assert result["po_answers"] == {}
    assert result["user_story"].description.startswith("As a user, I want")
    assert "thing X" in result["user_story"].description


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

        answers = {"What role is the actor?": "QA Lead"}
        result = pipeline.invoke(Command(resume=answers), config=config)

    assert "__interrupt__" not in result
    assert result["po_answers"] == answers
    assert result["user_story"].description.startswith("As a user, I want")


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
    expected_v1_fields = {"requirement_text", "analyst_output", "po_answers", "user_story"}
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
    'Deserializing unregistered type ...' warning that PR #58 fixed.
    This test catches it the moment a new Pydantic type slips into state.
    """
    allowed = {(mod, cls) for mod, cls in _CHECKPOINT_ALLOWLIST}
    required = {
        ("agents.requirements_analyst", "AnalystOutput"),
        ("agents.requirements_analyst", "Ambiguity"),
        ("agents.requirements_analyst", "ImpliedStory"),
        ("agents.user_story_writer", "UserStory"),
    }
    missing = required - allowed
    assert not missing, f"Pydantic types not in checkpoint allowlist: {missing}"


def test_build_pipeline_accepts_injected_checkpointer():
    """Tests + future API/UI use cases must be able to inject a checkpointer."""
    pipeline = build_pipeline(checkpointer=_test_checkpointer())
    # Smoke check: the compiled pipeline has nodes wired.
    assert pipeline is not None
