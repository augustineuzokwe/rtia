"""Tests for the Test Case Writer agent.

Mocks the LLM call — these tests cover the agent's contract (input
assembly, JSON parsing, schema validation), not Claude's behavior.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from agents.final_artifact import AcceptanceCriterion, TestCase
from agents.test_case_writer import TestCaseWriterOutput, write_test_cases
from agents.user_story_writer import UserStory

USER_STORY = UserStory(
    description="As a QA Lead, I want a real-time test run summary on the dashboard.",
    objective="I can monitor the latest run without manual refresh.",
    assumptions=["Auto-refresh interval is 30s (no explicit value given)."],
)

ACCEPTANCE_CRITERIA = [
    AcceptanceCriterion(
        given="I am an authenticated user on the QA Dashboard",
        when="I select a project",
        then="I see a summary of the most recent test run for that project",
    ),
    AcceptanceCriterion(
        given="I am viewing the dashboard summary",
        when="the auto-refresh interval elapses",
        then="the summary updates without a full page reload",
    ),
    AcceptanceCriterion(
        given="I am not authenticated",
        when="I navigate to the dashboard URL",
        then="I am redirected to the login page",
    ),
]

VALID_RESPONSE = {
    "cases": [
        {
            "scenario": "Authenticated QA Lead sees latest run summary",
            "type": "happy_path",
            "steps": [
                "Log in as a QA Lead user.",
                "Navigate to the QA Dashboard.",
                "Select a project with at least one completed test run.",
            ],
            "expected": "The dashboard displays the summary of the most recent test run.",
        },
        {
            "scenario": "Project has never been run",
            "type": "edge_case",
            "steps": [
                "Log in as a QA Lead.",
                "Select a project that has zero recorded test runs.",
            ],
            "expected": "The summary area shows an empty-state indicator.",
        },
        {
            "scenario": "Unauthenticated user is redirected",
            "type": "negative",
            "steps": [
                "Open a new browser session with no active login.",
                "Navigate directly to the dashboard URL.",
            ],
            "expected": "The user is redirected to the login page.",
        },
    ]
}


def _mock_invoke(payload: dict | str):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return patch(
        "agents.test_case_writer.ChatGoogleGenerativeAI.invoke",
        return_value=AIMessage(content=content),
    )


def test_returns_validated_test_cases():
    with _mock_invoke(VALID_RESPONSE):
        result = write_test_cases(USER_STORY, ACCEPTANCE_CRITERIA)

    assert isinstance(result, TestCaseWriterOutput)
    assert len(result.cases) == 3
    assert all(isinstance(c, TestCase) for c in result.cases)
    types = {c.type for c in result.cases}
    assert types == {"happy_path", "edge_case", "negative"}
    assert result.cases[0].steps[0].startswith("Log in")


def test_rejects_malformed_json():
    with _mock_invoke("not json"), pytest.raises(json.JSONDecodeError):
        write_test_cases(USER_STORY, ACCEPTANCE_CRITERIA)


def test_rejects_missing_required_field():
    bad = {
        "cases": [
            {
                "scenario": "Missing expected",
                "type": "happy_path",
                "steps": ["Do thing."],
                # expected omitted
            }
        ]
    }
    with _mock_invoke(bad), pytest.raises(ValidationError):
        write_test_cases(USER_STORY, ACCEPTANCE_CRITERIA)


def test_rejects_invalid_type_literal():
    bad = {
        "cases": [
            {
                "scenario": "Bad type",
                "type": "exploratory",  # not in TestCaseType literal
                "steps": ["Do thing."],
                "expected": "Outcome.",
            }
        ]
    }
    with _mock_invoke(bad), pytest.raises(ValidationError):
        write_test_cases(USER_STORY, ACCEPTANCE_CRITERIA)


def test_accepts_empty_cases_list():
    """An empty list should validate — the composer's renderer handles the
    placeholder. Forcing non-empty would silently mask Writer failures."""
    with _mock_invoke({"cases": []}):
        result = write_test_cases(USER_STORY, ACCEPTANCE_CRITERIA)

    assert result.cases == []


def test_prompt_includes_story_and_acceptance_criteria():
    """The user prompt must carry both the story and the ACs — losing
    either would silently strip the Writer's grounding."""
    captured: dict[str, object] = {}

    def fake_invoke(self, messages, **kwargs):  # noqa: ARG001 — match LangChain signature
        captured["messages"] = messages
        return AIMessage(content=json.dumps(VALID_RESPONSE))

    with patch("agents.test_case_writer.ChatGoogleGenerativeAI.invoke", new=fake_invoke):
        write_test_cases(USER_STORY, ACCEPTANCE_CRITERIA)

    human_text = captured["messages"][1].content
    assert "real-time test run summary" in human_text  # from USER_STORY.description
    assert "auto-refresh interval elapses" in human_text  # from AC #2
    assert "I am not authenticated" in human_text  # from AC #3


def test_handles_empty_acceptance_criteria():
    """If the AC Generator emitted nothing, the prompt should still render
    coherently — '(none)' placeholder rather than a broken format string."""
    captured: dict[str, object] = {}

    def fake_invoke(self, messages, **kwargs):  # noqa: ARG001
        captured["messages"] = messages
        return AIMessage(content=json.dumps({"cases": []}))

    with patch("agents.test_case_writer.ChatGoogleGenerativeAI.invoke", new=fake_invoke):
        write_test_cases(USER_STORY, [])

    human_text = captured["messages"][1].content
    assert "(none)" in human_text
