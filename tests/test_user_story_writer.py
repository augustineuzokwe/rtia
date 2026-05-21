"""Tests for the User Story Writer agent.

Mocks the LLM call — these tests cover the agent's contract (input
assembly, JSON parsing, schema validation), not Claude's behavior.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agents.requirements_analyst import Ambiguity, AnalystOutput
from agents.user_story_writer import UserStory, write_user_story

ANALYST_OUTPUT = AnalystOutput(
    intent="Give QA Leads a live view of test run health per project.",
    actors=["QA Lead", "dashboard"],
    ambiguities=[
        Ambiguity(question="What counts as 'real-time'?", severity="normal"),
        Ambiguity(question="Which authenticated user role?", severity="critical"),
    ],
)

PO_ANSWERS = {"Which authenticated user role?": "QA Lead"}

VALID_RESPONSE = {
    "description": (
        "As a QA Lead, I want to see a real-time summary of test runs for a selected project."
    ),
    "objective": "I can quickly assess release health.",
    "assumptions": ["Assumed 30s refresh for: What counts as 'real-time'?"],
}


def _mock_invoke(payload: dict | str):
    """Patch ChatGoogleGenerativeAI.invoke to return `payload` as the LLM response."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return patch(
        "agents.user_story_writer.ChatGoogleGenerativeAI.invoke",
        return_value=AIMessage(content=content),
    )


def test_returns_validated_user_story():
    with _mock_invoke(VALID_RESPONSE):
        result = write_user_story(ANALYST_OUTPUT, PO_ANSWERS)

    assert isinstance(result, UserStory)
    assert result.description.startswith("As a QA Lead, I want")
    assert "real-time summary" in result.description
    assert "release health" in result.objective
    assert result.assumptions == VALID_RESPONSE["assumptions"]


def test_rejects_malformed_json():
    with _mock_invoke("not json"), pytest.raises(json.JSONDecodeError):
        write_user_story(ANALYST_OUTPUT, PO_ANSWERS)


def test_rejects_response_missing_required_field():
    bad = {"description": "x", "objective": "y"}  # missing 'assumptions'
    with _mock_invoke(bad), pytest.raises(ValidationError):
        write_user_story(ANALYST_OUTPUT, PO_ANSWERS)


def test_rejects_response_with_legacy_role_want_benefit_schema():
    """Legacy schema (role/want/benefit) must fail loudly to surface stale callers."""
    legacy = {
        "role": "QA Lead",
        "want": "x",
        "benefit": "y",
        "assumptions": [],
    }
    with _mock_invoke(legacy), pytest.raises(ValidationError):
        write_user_story(ANALYST_OUTPUT, PO_ANSWERS)
