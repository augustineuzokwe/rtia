"""Tests for the Requirements Analyst agent.

Mocks the LLM call — these tests cover the agent's contract (prompt assembly,
JSON parsing, schema validation), not Claude's behavior. A live-API smoke test
will live separately.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agents.requirements_analyst import AnalystOutput, analyze_requirement

SAMPLE_REQUIREMENT = (
    "The QA Dashboard should show a real-time test run summary for a selected project."
)

VALID_RESPONSE = {
    "intent": "Give QA Leads a live view of test run health per project.",
    "actors": ["QA Lead", "unauthenticated user"],
    "ambiguities": ["What counts as 'real-time' — sub-second, or 30s refresh acceptable?"],
}


def _mock_invoke(payload: dict | str):
    """Build a patch context that makes ChatAnthropic.invoke return `payload`."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return patch(
        "agents.requirements_analyst.ChatAnthropic.invoke",
        return_value=AIMessage(content=content),
    )


def test_returns_validated_analyst_output():
    with _mock_invoke(VALID_RESPONSE):
        result = analyze_requirement(SAMPLE_REQUIREMENT)

    assert isinstance(result, AnalystOutput)
    assert result.intent == VALID_RESPONSE["intent"]
    assert result.actors == VALID_RESPONSE["actors"]
    assert result.ambiguities == VALID_RESPONSE["ambiguities"]


def test_rejects_malformed_json():
    with _mock_invoke("not json at all"), pytest.raises(json.JSONDecodeError):
        analyze_requirement(SAMPLE_REQUIREMENT)


def test_rejects_response_missing_required_field():
    bad = {"intent": "x", "actors": ["y"]}  # missing 'ambiguities'
    with _mock_invoke(bad), pytest.raises(ValidationError):
        analyze_requirement(SAMPLE_REQUIREMENT)
