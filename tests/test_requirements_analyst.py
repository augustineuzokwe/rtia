"""Tests for the Requirements Analyst agent.

Mocks the LLM call — these tests cover the agent's contract (prompt assembly,
JSON parsing, schema validation), not Claude's behavior.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agents.requirements_analyst import Ambiguity, AnalystOutput, analyze_requirement

SAMPLE_REQUIREMENT = (
    "The QA Dashboard should show a real-time test run summary for a selected project."
)

VALID_RESPONSE = {
    "intent": "Give QA Leads a live view of test run health per project.",
    "actors": ["QA Lead", "unauthenticated user"],
    "ambiguities": [
        {
            "question": "What counts as 'real-time' — sub-second or 30s refresh acceptable?",
            "severity": "normal",
        },
        {
            "question": "Which user role is meant by 'authenticated user'?",
            "severity": "critical",
        },
    ],
}


def _mock_invoke(payload: dict | str):
    """Patch ChatAnthropic.invoke to return `payload` as the LLM response."""
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
    assert len(result.ambiguities) == 2
    assert all(isinstance(a, Ambiguity) for a in result.ambiguities)
    assert {a.severity for a in result.ambiguities} == {"normal", "critical"}


def test_rejects_malformed_json():
    with _mock_invoke("not json at all"), pytest.raises(json.JSONDecodeError):
        analyze_requirement(SAMPLE_REQUIREMENT)


def test_rejects_response_missing_required_field():
    bad = {"intent": "x", "actors": ["y"]}  # missing 'ambiguities'
    with _mock_invoke(bad), pytest.raises(ValidationError):
        analyze_requirement(SAMPLE_REQUIREMENT)


def test_rejects_ambiguity_with_invalid_severity():
    bad = {
        "intent": "x",
        "actors": ["y"],
        "ambiguities": [{"question": "q?", "severity": "maybe"}],  # not 'critical'/'normal'
    }
    with _mock_invoke(bad), pytest.raises(ValidationError):
        analyze_requirement(SAMPLE_REQUIREMENT)
