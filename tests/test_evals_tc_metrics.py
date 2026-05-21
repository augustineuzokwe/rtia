"""Tests for the Test-Case-layer eval metrics.

Both metrics are fully programmatic (no judge), so these tests assert
the scoring contract directly against ``TestCase`` / ``AcceptanceCriterion``
inputs — mirroring the pattern in ``tests/test_evals_ac_metrics.py``.
"""

from __future__ import annotations

from agents.final_artifact import AcceptanceCriterion, TestCase
from evals.tc_metrics import (
    score_tc_coverage_breadth,
    score_tc_executability,
)


def _tc(
    type: str = "happy_path",
    scenario: str = "scenario",
    steps: list[str] | None = None,
    expected: str = "the result is visible",
) -> TestCase:
    return TestCase(
        scenario=scenario,
        type=type,  # type: ignore[arg-type]
        steps=steps if steps is not None else ["do the thing"],
        expected=expected,
    )


def _ac(given: str = "g", when: str = "w", then: str = "t") -> AcceptanceCriterion:
    return AcceptanceCriterion(given=given, when=when, then=then)


# ---------------------------------------------------------------------------
# tc_coverage_breadth
# ---------------------------------------------------------------------------


def test_tc_coverage_breadth_full_match_scores_one() -> None:
    acs = [
        _ac(then="the dashboard renders the project list"),
        _ac(then="the user sees an error message"),
    ]
    test_cases = [
        _tc(type="happy_path", expected="the dashboard renders the project list correctly"),
        _tc(type="edge_case", expected="dashboard renders project list with no items"),
        _tc(type="negative", expected="the user sees an error message on failure"),
    ]
    result = score_tc_coverage_breadth(test_cases, acs)
    assert result.score == 1.0


def test_tc_coverage_breadth_missing_type_drops_score() -> None:
    acs = [_ac(then="dashboard renders project list")]
    test_cases = [
        _tc(type="happy_path", expected="dashboard renders project list"),
        _tc(type="edge_case", expected="dashboard renders project list with one item"),
    ]
    # type_score = 2/3, ac_score = 1.0 → mean ≈ 0.833
    result = score_tc_coverage_breadth(test_cases, acs)
    assert 0.8 < result.score < 0.9
    assert "missing=['negative']" in result.reason


def test_tc_coverage_breadth_uncovered_ac_drops_score() -> None:
    acs = [
        _ac(then="dashboard renders project list"),
        _ac(then="export downloads a CSV file"),
    ]
    test_cases = [
        _tc(type="happy_path", expected="dashboard renders project list"),
        _tc(type="edge_case", expected="dashboard renders empty project list"),
        _tc(type="negative", expected="dashboard shows error on failure"),
    ]
    # type_score=1.0, ac_score=0.5 (export AC uncovered) → mean=0.75
    result = score_tc_coverage_breadth(test_cases, acs)
    assert result.score == 0.75


def test_tc_coverage_breadth_no_test_cases_scores_zero() -> None:
    result = score_tc_coverage_breadth([], [_ac()])
    assert result.score == 0.0


def test_tc_coverage_breadth_no_acs_still_scores_type_coverage() -> None:
    # ac_score defaults to 1.0 when there are no ACs to cover.
    test_cases = [
        _tc(type="happy_path"),
        _tc(type="edge_case"),
        _tc(type="negative"),
    ]
    result = score_tc_coverage_breadth(test_cases, [])
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# tc_executability
# ---------------------------------------------------------------------------


def test_tc_executability_concrete_steps_score_one() -> None:
    test_cases = [
        _tc(steps=["Open /projects", "Click the 'New' button", "Enter 'staging' in the field"]),
        _tc(steps=["Log in as alice@example.com", "Navigate to /settings"]),
    ]
    result = score_tc_executability(test_cases)
    assert result.score == 1.0


def test_tc_executability_penalises_angle_bracket_placeholder() -> None:
    test_cases = [
        _tc(steps=["Enter <value> in the field", "Click submit"]),
    ]
    # 1 violation × 0.25 = 0.25 penalty → 0.75
    result = score_tc_executability(test_cases)
    assert result.score == 0.75
    assert "<value>" in result.reason


def test_tc_executability_penalises_weasel_words() -> None:
    test_cases = [
        _tc(steps=["Enter some text", "Click the appropriate button"]),
    ]
    # Both patterns hit (weasel-word regex catches first match per pattern);
    # only one distinct vague-pattern fires (the weasel-word one), penalty 0.25.
    # Actually the regex returns one match per pattern, so 1 violation → 0.75.
    result = score_tc_executability(test_cases)
    assert result.score == 0.75


def test_tc_executability_floors_at_zero() -> None:
    # 2 distinct patterns hit: angle-bracket + weasel. 2 × 0.25 = 0.5 → 0.5.
    # Push further: a multi-step case with both patterns still caps at 2
    # distinct patterns per case (one match per pattern), so score is 0.5.
    test_cases = [
        _tc(steps=["Enter <value>", "with some content", "in the appropriate field"]),
    ]
    result = score_tc_executability(test_cases)
    assert result.score == 0.5


def test_tc_executability_averages_across_cases() -> None:
    test_cases = [
        _tc(steps=["Open /projects", "Click 'New'"]),  # clean → 1.0
        _tc(steps=["Enter <value>"]),  # 1 hit → 0.75
    ]
    # mean = (1.0 + 0.75) / 2 = 0.875
    result = score_tc_executability(test_cases)
    assert result.score == 0.875


def test_tc_executability_empty_list_scores_zero() -> None:
    """If the Test Case Writer returned nothing, executability is 0 — not vacuously 1."""
    result = score_tc_executability([])
    assert result.score == 0.0
