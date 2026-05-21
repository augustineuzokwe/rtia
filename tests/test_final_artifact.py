"""Tests for the FinalUserStory artifact contract + renderers.

These tests pin the v1 output shape that every downstream consumer
depends on: the rendered markdown sections, the JSON round-trip, and
the placeholder behavior for sections whose authoring agents haven't
landed yet.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents.final_artifact import (
    AcceptanceCriterion,
    FinalUserStory,
    TestCase,
)


def _full_artifact() -> FinalUserStory:
    return FinalUserStory(
        description="As an authenticated user, I want to see test results.",
        objective="I can monitor release health at a glance.",
        acceptance_criteria=[
            AcceptanceCriterion(
                given="I am an authenticated user",
                when="I open the dashboard",
                then="I see the latest test run summary",
            )
        ],
        test_cases=[
            TestCase(
                scenario="Dashboard renders for authenticated user",
                type="happy_path",
                steps=["Log in", "Navigate to /dashboard"],
                expected="Test run summary is visible within 2s",
            )
        ],
        assumptions=["Assumed 30s refresh interval"],
        metadata={"model": "gemini-2.5-flash"},
    )


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------


def test_final_user_story_constructs_with_minimum_required_fields():
    """description + objective are the only required fields (Story Writer outputs)."""
    artifact = FinalUserStory(
        description="As a user, I want X.",
        objective="Y is achieved.",
    )
    assert artifact.description == "As a user, I want X."
    assert artifact.objective == "Y is achieved."
    assert artifact.acceptance_criteria == []
    assert artifact.test_cases == []
    assert artifact.assumptions == []
    assert artifact.metadata == {}


def test_final_user_story_rejects_missing_required_field():
    """Removing description or objective must fail loudly."""
    with pytest.raises(ValidationError):
        FinalUserStory(description="missing objective")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        FinalUserStory(objective="missing description")  # type: ignore[call-arg]


def test_acceptance_criterion_requires_given_when_then():
    """The Given/When/Then trio is the BDD contract; all three required."""
    with pytest.raises(ValidationError):
        AcceptanceCriterion(given="x", when="y")  # type: ignore[call-arg]


def test_test_case_rejects_invalid_type():
    """Only happy_path / edge_case / negative are valid coverage types."""
    with pytest.raises(ValidationError):
        TestCase(
            scenario="bad",
            type="random",  # type: ignore[arg-type]
            steps=["x"],
            expected="y",
        )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_as_markdown_renders_all_four_sections_in_order():
    """Sections always appear in Description / Objective / AC / Test Cases order.

    Section order is the contract. A Jira/GH paste reads top-down, so
    a future change that reshuffles them is a real consumer break.
    """
    md = _full_artifact().as_markdown()
    desc = md.index("## Description")
    obj = md.index("## Objective")
    ac = md.index("## Acceptance Criteria")
    tc = md.index("## Test Cases")
    assert desc < obj < ac < tc, (
        f"Sections out of order: Description@{desc} Objective@{obj} AC@{ac} Test@{tc}"
    )


def test_as_markdown_shows_placeholder_when_ac_missing():
    """An empty acceptance_criteria list must render a placeholder, not be omitted.

    Empty lists must be visible — readers should SEE that AC isn't filled
    yet, not assume the agent ran and produced nothing.
    """
    artifact = FinalUserStory(description="x", objective="y")
    md = artifact.as_markdown()
    assert "## Acceptance Criteria" in md
    assert "AC Generator agent" in md  # placeholder mentions the responsible agent


def test_as_markdown_shows_placeholder_when_test_cases_missing():
    """Same contract as AC: empty test_cases renders a placeholder."""
    artifact = FinalUserStory(description="x", objective="y")
    md = artifact.as_markdown()
    assert "## Test Cases" in md
    assert "Test Case agent" in md


def test_as_markdown_emits_filled_acceptance_criteria_as_gwt_bullets():
    """When AC list is populated, each criterion renders as a Given/When/Then bullet."""
    md = _full_artifact().as_markdown()
    assert "**Given** I am an authenticated user" in md
    assert "**When** I open the dashboard" in md
    assert "**Then** I see the latest test run summary" in md


def test_as_markdown_emits_test_cases_with_type_tag():
    """Test cases must surface their coverage type in the rendered heading."""
    md = _full_artifact().as_markdown()
    assert "Dashboard renders for authenticated user" in md
    assert "_(happy_path)_" in md  # type appears next to the scenario heading


def test_as_markdown_omits_assumptions_when_empty():
    """A clean artifact without assumptions should not show a stub Assumptions heading."""
    artifact = FinalUserStory(description="x", objective="y")
    md = artifact.as_markdown()
    assert "## Assumptions" not in md


def test_as_markdown_omits_metadata_when_empty():
    """Metadata is optional context; absent metadata should not show a stub heading."""
    artifact = FinalUserStory(description="x", objective="y")
    md = artifact.as_markdown()
    assert "## Metadata" not in md


# ---------------------------------------------------------------------------
# JSON rendering
# ---------------------------------------------------------------------------


def test_as_json_roundtrips_via_pydantic():
    """JSON output must be parseable back into an equivalent FinalUserStory.

    Lossy serialization breaks API consumers; this test pins lossless
    behavior for every field including nested AC / TestCase Pydantic
    models.
    """
    original = _full_artifact()
    rebuilt = FinalUserStory.model_validate_json(original.as_json())
    assert rebuilt == original


def test_as_json_is_valid_parseable_json():
    """The raw string must be valid JSON regardless of Pydantic round-trip."""
    raw = _full_artifact().as_json()
    parsed = json.loads(raw)
    assert parsed["description"].startswith("As an authenticated user")
    assert isinstance(parsed["acceptance_criteria"], list)
    assert parsed["test_cases"][0]["type"] == "happy_path"
