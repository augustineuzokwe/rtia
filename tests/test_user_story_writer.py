"""Tests for the User Story Writer agent.

Mocks the LLM call - these tests cover the agent's contract (input
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


def test_write_user_story_renders_picked_story_in_prompt(monkeypatch):
    """when picked_story is set, the prompt must surface its title + summary."""
    from unittest.mock import MagicMock, patch

    from langchain_core.messages import AIMessage

    from agents.requirements_analyst import AnalystOutput, ImpliedStory
    from agents.user_story_writer import write_user_story

    captured: list[str] = []

    def _capture(**_kwargs):
        m = MagicMock()

        def _invoke(messages, **_):
            captured.append(messages[1].content)
            return AIMessage(content='{"description":"...","objective":"...","assumptions":[]}')

        m.invoke.side_effect = _invoke
        return m

    analyst = AnalystOutput(
        intent="multi-feature requirement",
        actors=["QA engineer"],
        ambiguities=[],
        implied_stories=[
            ImpliedStory(title="Quarantined tests dashboard", summary="dashboard story"),
        ],
    )
    picked = ImpliedStory(title="Quarantined tests dashboard", summary="dashboard story")

    with patch("agents.user_story_writer.ChatGoogleGenerativeAI", side_effect=_capture):
        write_user_story(analyst, {"Which?": "dashboard"}, picked_story=picked)

    assert len(captured) == 1
    rendered = captured[0]
    assert "Picked story" in rendered
    assert "Quarantined tests dashboard" in rendered
    assert "dashboard story" in rendered

    # And the "(none)" placeholder when no pick is supplied.
    captured.clear()
    with patch("agents.user_story_writer.ChatGoogleGenerativeAI", side_effect=_capture):
        write_user_story(analyst, {"Which?": "all"})
    assert "(none)" in captured[0]
