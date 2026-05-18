"""Tests for the RTIA pipeline graph.

Mocks the underlying LLM calls so tests run offline and deterministically.
Covers graph wiring: happy-path flow-through through the Story Writer,
pause at critical ambiguities, and resume with PO answers (also routing
through the Story Writer).
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from langgraph.types import Command

from agents.graph import build_pipeline


def _fake_analyst_response(ambiguities: list[dict]) -> dict:
    """Build a minimal Analyst response payload with the given ambiguities."""
    return {
        "intent": "Goal X",
        "actors": ["User"],
        "ambiguities": ambiguities,
    }


FAKE_STORY_RESPONSE = {
    "role": "User",
    "want": "do thing X",
    "benefit": "outcome Y is achieved",
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
        pipeline = build_pipeline()
        config = {"configurable": {"thread_id": "test-flow-through"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" not in result
    assert result["analyst_output"].intent == "Goal X"
    assert result["po_answers"] == {}
    assert result["user_story"].role == "User"
    assert result["user_story"].want == "do thing X"


def test_pipeline_pauses_when_critical_ambiguity_present():
    """A critical ambiguity should pause the graph before the Story Writer."""
    analyst = _fake_analyst_response(
        [
            {"question": "What role is the actor?", "severity": "critical"},
            {"question": "Display style?", "severity": "normal"},
        ]
    )

    with _mock_pipeline_llms(analyst):
        pipeline = build_pipeline()
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
        pipeline = build_pipeline()
        config = {"configurable": {"thread_id": "test-resume"}}
        pipeline.invoke({"requirement_text": "some requirement"}, config=config)

        answers = {"What role is the actor?": "QA Lead"}
        result = pipeline.invoke(Command(resume=answers), config=config)

    assert "__interrupt__" not in result
    assert result["po_answers"] == answers
    assert result["user_story"].role == "User"
