"""Tests for the AC Generator agent.

Mocks the LLM call — these tests cover the agent's contract (input
assembly, JSON parsing, schema validation), not Claude's behavior.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agents.ac_generator import AcGeneratorOutput, generate_acceptance_criteria
from agents.final_artifact import AcceptanceCriterion
from agents.requirements_analyst import AnalystOutput
from agents.user_story_writer import UserStory

USER_STORY = UserStory(
    description="As a QA Lead, I want a real-time test run summary on the dashboard.",
    objective="I can monitor the latest run without manual refresh.",
    assumptions=["Auto-refresh interval is 30s (no explicit value given)."],
)

ANALYST_OUTPUT = AnalystOutput(
    intent="Let an authenticated QA Lead monitor the most recent test run.",
    actors=["QA Lead", "unauthenticated user"],
    ambiguities=[],
)

PO_ANSWERS: dict[str, str] = {}

VALID_RESPONSE = {
    "criteria": [
        {
            "given": "I am an authenticated user on the QA Dashboard",
            "when": "I select a project",
            "then": "I see a summary of the most recent test run for that project",
        },
        {
            "given": "I am viewing the dashboard summary",
            "when": "the auto-refresh interval elapses",
            "then": "the summary updates without a full page reload",
        },
        {
            "given": "I am not authenticated",
            "when": "I navigate to the dashboard URL",
            "then": "I am redirected to the login page",
        },
    ]
}


def _mock_invoke(payload: dict | str):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return patch(
        "agents.ac_generator.ChatGoogleGenerativeAI.invoke",
        return_value=AIMessage(content=content),
    )


def test_returns_validated_acs():
    with _mock_invoke(VALID_RESPONSE):
        result = generate_acceptance_criteria(USER_STORY, ANALYST_OUTPUT, PO_ANSWERS)

    assert isinstance(result, AcGeneratorOutput)
    assert len(result.criteria) == 3
    assert all(isinstance(c, AcceptanceCriterion) for c in result.criteria)
    assert result.criteria[0].given.startswith("I am an authenticated user")
    assert result.criteria[2].then.endswith("login page")


def test_rejects_malformed_json():
    with _mock_invoke("not json"), pytest.raises(json.JSONDecodeError):
        generate_acceptance_criteria(USER_STORY, ANALYST_OUTPUT, PO_ANSWERS)


def test_rejects_missing_required_field():
    bad = {"criteria": [{"given": "g", "when": "w"}]}  # missing "then"
    with _mock_invoke(bad), pytest.raises(ValidationError):
        generate_acceptance_criteria(USER_STORY, ANALYST_OUTPUT, PO_ANSWERS)


def test_accepts_empty_criteria_list():
    """An empty list should validate — the composer's renderer handles the
    placeholder. Forcing non-empty would silently mask Generator failures."""
    with _mock_invoke({"criteria": []}):
        result = generate_acceptance_criteria(USER_STORY, ANALYST_OUTPUT, PO_ANSWERS)

    assert result.criteria == []


def test_prompt_includes_story_and_analyst_context():
    """The user prompt must carry both the story and the Analyst context;
    losing either was a real risk during refactors."""
    captured: dict[str, object] = {}

    def fake_invoke(self, messages, **kwargs):  # noqa: ARG001 — match LangChain signature
        captured["messages"] = messages
        return AIMessage(content=json.dumps(VALID_RESPONSE))

    with patch("agents.ac_generator.ChatGoogleGenerativeAI.invoke", new=fake_invoke):
        generate_acceptance_criteria(USER_STORY, ANALYST_OUTPUT, {"q?": "a"})

    human_text = captured["messages"][1].content
    assert "real-time test run summary" in human_text  # from USER_STORY.description
    assert "authenticated QA Lead" in human_text  # from ANALYST_OUTPUT.intent
    assert "QA Lead" in human_text  # actor passed through
    assert "Q: q?" in human_text  # PO answers section
