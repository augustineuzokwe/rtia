"""Tests for the AC-layer eval metrics.

Judge calls are stubbed. The tests assert the scoring contract — does each
metric penalise the failure mode it is meant to catch — not the calibration
of judge prompts (calibration lives in the live baseline run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.ac_generator import AcGeneratorOutput
from agents.final_artifact import AcceptanceCriterion
from agents.user_story_writer import UserStory
from evals.ac_metrics import (
    _AcClassification,
    score_ac_coverage,
    score_ac_faithfulness,
    score_ac_testability,
)
from evals.dataset import ExpectedAcceptanceCriteria


@dataclass
class _StubJudge:
    """Deterministic stand-in for ClaudeJudge.

    For coverage: keyed by AC.then, returns the verdict to use.
    For faithfulness (GEval): unused — faithfulness needs a real GEval-
    compatible judge to ``measure()`` a test case. The faithfulness metric
    is exercised end-to-end in the live baseline run, not here.
    """

    coverage_verdicts: dict[str, _AcClassification]

    def generate(self, prompt: str, schema: type[Any] | None = None) -> Any:
        if schema is _AcClassification:
            for then_clause, verdict in self.coverage_verdicts.items():
                if then_clause in prompt:
                    return verdict
            return _AcClassification(
                matched_required_category=None,
                matched_out_of_scope=None,
                reason="no stub rule matched",
            )
        raise AssertionError(f"unexpected schema in stub judge: {schema!r}")


def _expected_acs(
    required: list[str], count: int = 3, tolerance: int = 1, out: list[str] = ()
) -> ExpectedAcceptanceCriteria:
    return ExpectedAcceptanceCriteria(
        required_categories=list(required),
        expected_count=count,
        count_tolerance=tolerance,
        out_of_scope=list(out),
    )


def _ac(given: str = "g", when: str = "w", then: str = "t") -> AcceptanceCriterion:
    return AcceptanceCriterion(given=given, when=when, then=then)


# ---------------------------------------------------------------------------
# ac_coverage
# ---------------------------------------------------------------------------


def test_ac_coverage_full_match_scores_one() -> None:
    expected = _expected_acs(["category-a", "category-b"])
    generated = AcGeneratorOutput(
        criteria=[
            _ac(then="covers a"),
            _ac(then="covers b"),
        ]
    )
    judge = _StubJudge(
        {
            "covers a": _AcClassification(
                matched_required_category="category-a", matched_out_of_scope=None, reason=""
            ),
            "covers b": _AcClassification(
                matched_required_category="category-b", matched_out_of_scope=None, reason=""
            ),
        }
    )
    result = score_ac_coverage(generated, expected, judge)
    assert result.score == 1.0


def test_ac_coverage_missing_category_drops_recall() -> None:
    expected = _expected_acs(["category-a", "category-b"])
    generated = AcGeneratorOutput(criteria=[_ac(then="covers a")])
    judge = _StubJudge(
        {
            "covers a": _AcClassification(
                matched_required_category="category-a", matched_out_of_scope=None, reason=""
            )
        }
    )
    result = score_ac_coverage(generated, expected, judge)
    # precision=1.0, recall=0.5, F1≈0.667
    assert 0.6 < result.score < 0.7
    assert "missing=['category-b']" in result.reason


def test_ac_coverage_out_of_scope_drops_precision() -> None:
    expected = _expected_acs(["category-a"], out=["forbidden-thing"])
    generated = AcGeneratorOutput(
        criteria=[
            _ac(then="covers a"),
            _ac(then="does forbidden"),
        ]
    )
    judge = _StubJudge(
        {
            "covers a": _AcClassification(
                matched_required_category="category-a", matched_out_of_scope=None, reason=""
            ),
            "does forbidden": _AcClassification(
                matched_required_category=None,
                matched_out_of_scope="forbidden-thing",
                reason="",
            ),
        }
    )
    result = score_ac_coverage(generated, expected, judge)
    # precision=0.5, recall=1.0, F1≈0.667
    assert 0.6 < result.score < 0.7
    assert "out_of_scope_hits=" in result.reason


def test_ac_coverage_empty_when_no_categories_expected() -> None:
    expected = _expected_acs([], count=0, tolerance=0, out=[])
    generated = AcGeneratorOutput(criteria=[])
    result = score_ac_coverage(generated, expected, _StubJudge({}))
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# ac_testability (no judge — fully programmatic)
# ---------------------------------------------------------------------------


def test_ac_testability_well_formed_acs_score_one() -> None:
    generated = AcGeneratorOutput(
        criteria=[
            _ac(given="I am logged in", when="I click save", then="my changes persist"),
            _ac(given="I am offline", when="I click save", then="I see an error"),
        ]
    )
    result = score_ac_testability(generated)
    assert result.score == 1.0


def test_ac_testability_penalises_empty_field() -> None:
    generated = AcGeneratorOutput(
        criteria=[
            _ac(given="g1", when="w1", then="t1"),
            _ac(given="", when="w2", then="t2"),
        ]
    )
    result = score_ac_testability(generated)
    assert result.score == 0.5  # 1 of 2 passing
    assert "given is empty" in result.reason


def test_ac_testability_penalises_non_observable_then() -> None:
    generated = AcGeneratorOutput(
        criteria=[
            _ac(given="g", when="w", then="the user feels confident in the system"),
        ]
    )
    result = score_ac_testability(generated)
    assert result.score == 0.0
    assert "non-observable verb" in result.reason


def test_ac_testability_penalises_paragraph_dumped_then() -> None:
    long_then = "the user sees the dashboard " * 20
    generated = AcGeneratorOutput(criteria=[_ac(then=long_then)])
    result = score_ac_testability(generated)
    assert result.score == 0.0
    assert "exceeds" in result.reason


def test_ac_testability_empty_list_scores_zero_not_one() -> None:
    """If the AC Generator returned nothing, testability is 0 — not vacuously 1."""
    generated = AcGeneratorOutput(criteria=[])
    result = score_ac_testability(generated)
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# ac_faithfulness — empty-case only (GEval path needs live judge to exercise)
# ---------------------------------------------------------------------------


def test_ac_faithfulness_empty_list_scores_zero() -> None:
    story = UserStory(
        description="As a user, I want X.", objective="Y is achieved.", assumptions=[]
    )
    generated = AcGeneratorOutput(criteria=[])
    # Judge unused for the empty path — None would crash if called, which is
    # the guarantee we want.
    result = score_ac_faithfulness(generated, story, judge=None)  # type: ignore[arg-type]
    assert result.score == 0.0
