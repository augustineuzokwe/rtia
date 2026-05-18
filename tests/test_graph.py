"""Tests for the RTIA pipeline graph.

Mocks the underlying LLM call so tests run offline and deterministically.
Covers three graph behaviours: happy-path flow-through, pause at critical
ambiguities, and resume with PO answers.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langgraph.types import Command

from agents.graph import build_pipeline


def _fake_response(ambiguities: list[dict]) -> dict:
    """Build a minimal Analyst response payload with the given ambiguities."""
    return {
        "intent": "Goal X",
        "actors": ["User"],
        "ambiguities": ambiguities,
    }


def _mock_invoke(payload: dict):
    """Patch ChatAnthropic.invoke to return the payload as the LLM response."""
    return patch(
        "agents.requirements_analyst.ChatAnthropic.invoke",
        return_value=AIMessage(content=json.dumps(payload)),
    )


def test_pipeline_flows_through_when_no_critical_ambiguities():
    """All-normal ambiguities should not pause the graph."""
    response = _fake_response([{"question": "Style preference?", "severity": "normal"}])

    with _mock_invoke(response):
        pipeline = build_pipeline()
        config = {"configurable": {"thread_id": "test-flow-through"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" not in result
    assert result["analyst_output"].intent == "Goal X"
    assert result["po_answers"] == {}


def test_pipeline_pauses_when_critical_ambiguity_present():
    """A critical ambiguity should pause the graph at the PO checkpoint."""
    response = _fake_response(
        [
            {"question": "What role is the actor?", "severity": "critical"},
            {"question": "Display style?", "severity": "normal"},
        ]
    )

    with _mock_invoke(response):
        pipeline = build_pipeline()
        config = {"configurable": {"thread_id": "test-pause"}}
        result = pipeline.invoke({"requirement_text": "some requirement"}, config=config)

    assert "__interrupt__" in result
    interrupt_value = result["__interrupt__"][0].value
    assert interrupt_value["critical_ambiguities"] == ["What role is the actor?"]


def test_pipeline_resumes_with_po_answers():
    """Resuming the paused graph with answers should populate po_answers and complete."""
    response = _fake_response([{"question": "What role is the actor?", "severity": "critical"}])

    with _mock_invoke(response):
        pipeline = build_pipeline()
        config = {"configurable": {"thread_id": "test-resume"}}
        pipeline.invoke({"requirement_text": "some requirement"}, config=config)

        answers = {"What role is the actor?": "QA Lead"}
        result = pipeline.invoke(Command(resume=answers), config=config)

    assert "__interrupt__" not in result
    assert result["po_answers"] == answers
