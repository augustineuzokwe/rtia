"""Metric tests for the Analyst eval suite.

The judge is mocked — we are validating the *scoring contract*, not the
calibration of judge prompts (calibration belongs to the live baseline
run captured in evals/baselines.md). Each test asserts the metric
penalises the failure mode it is meant to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.requirements_analyst import Ambiguity, AnalystOutput
from evals.dataset import ExpectedAnalystOutput
from evals.metrics import (
    _ActorAlignment,
    _CategoryCoverage,
    score_actor_set_completeness,
    score_ambiguity_discipline,
)


@dataclass
class _StubJudge:
    """Deterministic stand-in for GeminiJudge.

    The real judge is an LLM. For unit-level metric tests we control its
    answers explicitly so the test asserts metric arithmetic, not judge
    behaviour.
    """

    actor_responses: dict[str, str | None]
    """actual_label -> matched expected label (or None for no synonym match)."""

    ambiguity_responses: dict[str, _CategoryCoverage]
    """ambiguity question -> verdict."""

    def generate(self, prompt: str, schema: type[Any] | None = None) -> Any:
        if schema is _ActorAlignment:
            for actual, match in self.actor_responses.items():
                if repr(actual) in prompt:
                    return _ActorAlignment(matched_expected_label=match, reason="stub")
            return _ActorAlignment(matched_expected_label=None, reason="no rule")
        if schema is _CategoryCoverage:
            for question, verdict in self.ambiguity_responses.items():
                if repr(question) in prompt:
                    return verdict
            return _CategoryCoverage(matched_category=None, is_in_scope=False, reason="no rule")
        raise AssertionError(f"unexpected schema in stub judge: {schema!r}")


# ---------------------------------------------------------------------------
# actor_set_completeness
# ---------------------------------------------------------------------------


def _expected(
    actors: list[str], cats: list[str] = (), implied: list[str] = ()
) -> ExpectedAnalystOutput:
    return ExpectedAnalystOutput(
        intent="ignored in these tests",
        actors=list(actors),
        ambiguity_categories=list(cats),
        implied_story_titles=list(implied),
    )


def _analyst(actors: list[str], ambiguities: list[Ambiguity] | None = None) -> AnalystOutput:
    return AnalystOutput(
        intent="ignored",
        actors=list(actors),
        ambiguities=ambiguities or [],
        implied_stories=[],
    )


def test_actor_set_perfect_match_scores_one() -> None:
    expected = _expected(["QA Lead", "unauthenticated user"])
    actual = _analyst(["QA Lead", "unauthenticated user"])
    result = score_actor_set_completeness(actual, expected, _StubJudge({}, {}))
    assert result.score == 1.0


def test_actor_set_missing_actor_drops_recall() -> None:
    expected = _expected(["QA Lead", "unauthenticated user"])
    actual = _analyst(["QA Lead"])
    result = score_actor_set_completeness(actual, expected, _StubJudge({}, {}))
    # precision=1.0, recall=0.5, F1 ≈ 0.667
    assert 0.6 < result.score < 0.7
    assert "missing=['unauthenticated user']" in result.reason


def test_actor_set_invented_actor_drops_precision() -> None:
    expected = _expected(["QA Lead"])
    actual = _analyst(["QA Lead", "data analyst"])
    judge = _StubJudge({"data analyst": None}, {})  # no synonym for the invented actor
    result = score_actor_set_completeness(actual, expected, judge)
    # precision=0.5, recall=1.0, F1 ≈ 0.667
    assert 0.6 < result.score < 0.7
    assert "invented=['data analyst']" in result.reason


def test_actor_set_synonym_resolved_by_judge() -> None:
    expected = _expected(["QA Lead"])
    actual = _analyst(["test lead"])
    judge = _StubJudge({"test lead": "QA Lead"}, {})
    result = score_actor_set_completeness(actual, expected, judge)
    assert result.score == 1.0


# ---------------------------------------------------------------------------
# ambiguity_discipline
# ---------------------------------------------------------------------------


def test_ambiguity_discipline_well_structured_no_ambiguities_expected() -> None:
    expected = _expected(actors=[], cats=[])
    actual = _analyst(actors=[], ambiguities=[])
    result = score_ambiguity_discipline(actual, expected, _StubJudge({}, {}))
    assert result.score == 1.0


def test_ambiguity_discipline_penalises_overflagging_on_clean_sample() -> None:
    """Analyst flagged UX detail on a sample the spec says expects zero ambiguities."""
    expected = _expected(actors=[], cats=[])
    actual = _analyst(
        actors=[],
        ambiguities=[Ambiguity(question="Should refresh show a spinner?", severity="normal")],
    )
    result = score_ambiguity_discipline(actual, expected, _StubJudge({}, {}))
    assert result.score == 0.0
    assert "out-of-scope" in result.reason


def test_ambiguity_discipline_full_coverage_scores_one() -> None:
    expected = _expected(actors=[], cats=["actor scoping", "manager visibility shape"])
    actual = _analyst(
        actors=[],
        ambiguities=[
            Ambiguity(question="Are managers a separate role from the team?", severity="critical"),
            Ambiguity(question="What does 'visibility' mean concretely?", severity="normal"),
        ],
    )
    judge = _StubJudge(
        {},
        {
            "Are managers a separate role from the team?": _CategoryCoverage(
                matched_category="actor scoping", is_in_scope=True, reason="stub"
            ),
            "What does 'visibility' mean concretely?": _CategoryCoverage(
                matched_category="manager visibility shape", is_in_scope=True, reason="stub"
            ),
        },
    )
    result = score_ambiguity_discipline(actual, expected, judge)
    assert result.score == 1.0


def test_ambiguity_discipline_mixed_in_and_out_of_scope() -> None:
    expected = _expected(actors=[], cats=["actor scoping"])
    actual = _analyst(
        actors=[],
        ambiguities=[
            Ambiguity(question="Are managers a separate role from the team?", severity="critical"),
            Ambiguity(question="Should refresh show a spinner?", severity="normal"),
        ],
    )
    judge = _StubJudge(
        {},
        {
            "Are managers a separate role from the team?": _CategoryCoverage(
                matched_category="actor scoping", is_in_scope=True, reason="stub"
            ),
            "Should refresh show a spinner?": _CategoryCoverage(
                matched_category=None, is_in_scope=False, reason="UX detail"
            ),
        },
    )
    result = score_ambiguity_discipline(actual, expected, judge)
    # precision=1/2=0.5, recall=1/1=1.0, F1≈0.667
    assert 0.6 < result.score < 0.7
