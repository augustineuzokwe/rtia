"""Unit tests for the Reviewer agent.

Mocks the underlying LLM call so tests run offline and deterministically.
Covers the public API (`review_artifact`) and the `ReviewReport` Pydantic
schema — including empty-list responses (valid "no issues found" case).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from agents.final_artifact import AcceptanceCriterion, FinalUserStory, TestCase
from agents.reviewer import ReviewReport, review_artifact


def _fake_llm(payload: dict):
    """Build a fake ChatGoogleGenerativeAI whose instances return `payload`."""

    def _factory(**_kwargs):
        instance = MagicMock()
        instance.invoke.return_value = AIMessage(content=json.dumps(payload))
        return instance

    return _factory


def _minimal_artifact(**kwargs) -> FinalUserStory:
    defaults = dict(
        description="As a user, I want to do X.",
        objective="Outcome Y is achieved.",
        acceptance_criteria=[
            AcceptanceCriterion(
                given="the user is logged in",
                when="they do X",
                then="outcome Y appears",
            )
        ],
        test_cases=[
            TestCase(
                scenario="Happy path — user does X",
                type="happy_path",
                steps=["Log in.", "Do X."],
                expected="Outcome Y is visible.",
            )
        ],
        assumptions=[],
        metadata={},
    )
    defaults.update(kwargs)
    return FinalUserStory(**defaults)


_STRONG_REPORT = {
    "coverage_gaps": [],
    "weak_acs": [],
    "untestable_criteria": [],
    "recommendations": [],
    "overall_quality": "strong",
}

_WEAK_REPORT = {
    "coverage_gaps": ["No AC covers the 30-second auto-refresh cadence."],
    "weak_acs": [
        "AC1 ('Then the data updates') — omits the 30-second cadence and "
        "'without full page reload' constraint."
    ],
    "untestable_criteria": [],
    "recommendations": [
        "Revise AC1: Given I am viewing the dashboard; When 30 seconds have elapsed; "
        "Then the summary updates without a full page reload."
    ],
    "overall_quality": "acceptable",
}


def test_review_artifact_returns_strong_report_for_solid_artifact():
    artifact = _minimal_artifact()
    with patch("agents.reviewer.ChatGoogleGenerativeAI", side_effect=_fake_llm(_STRONG_REPORT)):
        report = review_artifact("Do X and show outcome Y.", artifact)

    assert isinstance(report, ReviewReport)
    assert report.overall_quality == "strong"
    assert report.coverage_gaps == []
    assert report.weak_acs == []
    assert report.untestable_criteria == []
    assert report.recommendations == []


def test_review_artifact_flags_weak_acs_and_coverage_gaps():
    artifact = _minimal_artifact()
    with patch("agents.reviewer.ChatGoogleGenerativeAI", side_effect=_fake_llm(_WEAK_REPORT)):
        report = review_artifact("Do X, refresh every 30 seconds.", artifact)

    assert report.overall_quality == "acceptable"
    assert len(report.coverage_gaps) == 1
    assert "30-second" in report.coverage_gaps[0]
    assert len(report.weak_acs) == 1
    assert len(report.recommendations) == 1
    assert "30 seconds" in report.recommendations[0]


def test_review_artifact_accepts_needs_work_quality():
    payload = {
        "coverage_gaps": ["No AC covers user authentication."],
        "weak_acs": [],
        "untestable_criteria": [],
        "recommendations": ["Add an AC for authentication."],
        "overall_quality": "needs_work",
    }
    artifact = _minimal_artifact()
    with patch("agents.reviewer.ChatGoogleGenerativeAI", side_effect=_fake_llm(payload)):
        report = review_artifact("Authenticated users do X.", artifact)

    assert report.overall_quality == "needs_work"
    assert len(report.coverage_gaps) == 1


def test_review_artifact_with_no_acs_or_test_cases():
    """Reviewer still runs when the artifact has no ACs/TCs; it should flag the gap."""
    empty_artifact = FinalUserStory(
        description="As a user, I want to do X.",
        objective="Outcome Y.",
        acceptance_criteria=[],
        test_cases=[],
        assumptions=[],
        metadata={},
    )
    payload = {
        "coverage_gaps": ["No ACs produced."],
        "weak_acs": [],
        "untestable_criteria": [],
        "recommendations": ["Add ACs."],
        "overall_quality": "needs_work",
    }
    with patch("agents.reviewer.ChatGoogleGenerativeAI", side_effect=_fake_llm(payload)):
        report = review_artifact("Do X.", empty_artifact)

    assert report.overall_quality == "needs_work"
    assert report.coverage_gaps == ["No ACs produced."]
