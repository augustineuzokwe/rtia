"""Acceptance-Criteria-layer metrics for the per-agent eval suite.

Two metrics, each scored in [0.0, 1.0]:

- ``ac_coverage`` — F1 of (required categories covered) vs (in-scope ACs / total
  ACs). Catches both **under-coverage** (a required category has no AC) and
  **scope leakage** (ACs that match a deliberately out-of-scope behaviour).
  Uses the match judge — one short classification call per generated AC.
- ``ac_testability`` — programmatic. Each AC must have non-empty
  given/when/then, no non-observable verbs in ``then`` ("feels", "thinks",
  "knows"), and each field must fit a sane length cap. No LLM calls.

History — a third metric (``ac_faithfulness``) was deleted in the Gemini
cutover (ADR-0006). It used deepeval's GEval to flag invented scope vs.
the user story; the metric was documented as structurally pessimistic
(scored ACs against Story Writer output only, ignoring Analyst context
the AC Generator legitimately reads). A deterministic alternative is
captured under Epic #92 if needed later.

Metric API mirrors ``evals/metrics.py``: each function returns a
``MetricResult`` (name + score + reason).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agents.ac_generator import AcGeneratorOutput
from agents.final_artifact import AcceptanceCriterion
from evals.dataset import ExpectedAcceptanceCriteria
from evals.judge import GeminiJudge
from evals.metrics import MetricResult

# ---------------------------------------------------------------------------
# ac_coverage
# ---------------------------------------------------------------------------


class _AcClassification(BaseModel):
    """Judge verdict for a single generated AC against the ground-truth bundle."""

    matched_required_category: str | None = Field(
        description=(
            "Exact label of the required AC category this AC covers, or null "
            "if the AC does not cover any required category."
        )
    )
    matched_out_of_scope: str | None = Field(
        description=(
            "Exact label of the out-of-scope behaviour this AC matches, or "
            "null if the AC is not out-of-scope."
        )
    )
    reason: str


def _classify_ac(
    ac: AcceptanceCriterion,
    expected: ExpectedAcceptanceCriteria,
    judge: GeminiJudge,
) -> _AcClassification:
    prompt = (
        "You are classifying one acceptance criterion against a ground-truth "
        "bundle for the story it belongs to.\n\n"
        "The bundle has TWO lists:\n"
        "- Required AC categories — behaviours that must be covered by some "
        "AC. An AC 'matches' a category when it tests that behaviour "
        "(wording will vary; match by meaning).\n"
        "- Out-of-scope behaviours — behaviours the AC Generator must NOT "
        "produce ACs for. An AC matches one of these when it tests that "
        "out-of-scope behaviour (again, by meaning).\n\n"
        "An AC may legitimately match neither list (in-scope but not "
        "required-listed) — in that case both fields are null.\n\n"
        f"AC under classification:\n"
        f"  Given: {ac.given}\n"
        f"  When:  {ac.when}\n"
        f"  Then:  {ac.then}\n\n"
        f"Required categories: {expected.required_categories!r}\n"
        f"Out-of-scope behaviours: {expected.out_of_scope!r}\n\n"
        "Return JSON with matched_required_category (exact label or null), "
        "matched_out_of_scope (exact label or null), and a short reason."
    )
    return judge.generate(prompt, schema=_AcClassification)


def score_ac_coverage(
    generated: AcGeneratorOutput,
    expected: ExpectedAcceptanceCriteria,
    judge: GeminiJudge,
) -> MetricResult:
    """F1 of category-coverage recall × in-scope precision.

    - **recall** = (required categories covered by some AC) / (total required
      categories). Catches under-coverage.
    - **precision** = (ACs that are not out-of-scope) / (total ACs). Catches
      scope leakage / invented ACs that match a deliberately deferred topic.

    The count-tolerance from the ground truth is reported in the reason but
    does NOT directly affect the score — coverage already penalises both
    under- and over-count via the F1 components.
    """
    if not generated.criteria:
        if not expected.required_categories:
            return MetricResult(
                name="ac_coverage",
                score=1.0,
                reason="No ACs expected; AC Generator emitted none.",
            )
        return MetricResult(
            name="ac_coverage",
            score=0.0,
            reason=(
                f"Expected {len(expected.required_categories)} categories "
                f"({expected.required_categories}); AC Generator emitted none."
            ),
        )

    covered: set[str] = set()
    out_of_scope_hits: list[str] = []
    for ac in generated.criteria:
        verdict = _classify_ac(ac, expected, judge)
        if verdict.matched_required_category in expected.required_categories:
            covered.add(verdict.matched_required_category)
        if verdict.matched_out_of_scope in expected.out_of_scope:
            out_of_scope_hits.append(ac.then[:50])

    total_acs = len(generated.criteria)
    in_scope = total_acs - len(out_of_scope_hits)
    precision = in_scope / total_acs if total_acs else 1.0
    recall = (
        len(covered) / len(expected.required_categories) if expected.required_categories else 1.0
    )
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    missing = [c for c in expected.required_categories if c not in covered]
    count_band = (
        expected.expected_count - expected.count_tolerance,
        expected.expected_count + expected.count_tolerance,
    )
    reason = (
        f"recall={recall:.2f} ({len(covered)}/{len(expected.required_categories)} covered); "
        f"precision={precision:.2f} ({in_scope}/{total_acs} in-scope); "
        f"F1={f1:.2f}. missing={missing}, out_of_scope_hits={out_of_scope_hits}. "
        f"count={total_acs} (expected {count_band[0]}-{count_band[1]})."
    )
    return MetricResult(name="ac_coverage", score=f1, reason=reason)


# ---------------------------------------------------------------------------
# ac_testability
# ---------------------------------------------------------------------------

# Words that signal non-observable assertions in a Then clause. Hand-curated;
# expand only with concrete evidence from real failures, not speculatively.
_NON_OBSERVABLE_VERBS = re.compile(
    r"\b(feels?|thinks?|knows?|understands?|believes?|intuits?|senses?)\b",
    re.IGNORECASE,
)

# Per-field character cap. Wide enough for natural English ACs, narrow enough
# to flag the "paragraph dumped into a single field" failure mode.
_MAX_FIELD_LEN = 240


def _testability_violations(ac: AcceptanceCriterion) -> list[str]:
    failures: list[str] = []
    for field_name in ("given", "when", "then"):
        value = getattr(ac, field_name).strip()
        if not value:
            failures.append(f"{field_name} is empty")
        if len(value) > _MAX_FIELD_LEN:
            failures.append(f"{field_name} exceeds {_MAX_FIELD_LEN} chars ({len(value)})")
    if _NON_OBSERVABLE_VERBS.search(ac.then):
        failures.append("then contains a non-observable verb (feels/thinks/knows/…)")
    return failures


def score_ac_testability(generated: AcGeneratorOutput) -> MetricResult:
    """Fraction of ACs that pass every programmatic well-formedness check.

    No LLM calls — these are properties we can check from the structure
    directly. Keeps testability cheap to evaluate (and the metric available
    in fully-offline test runs).
    """
    if not generated.criteria:
        return MetricResult(
            name="ac_testability",
            score=0.0,
            reason="No ACs to score.",
        )
    bad: list[str] = []
    for i, ac in enumerate(generated.criteria):
        violations = _testability_violations(ac)
        if violations:
            bad.append(f"AC#{i}: {', '.join(violations)}")

    passing = len(generated.criteria) - len(bad)
    score = passing / len(generated.criteria)
    reason = f"{passing}/{len(generated.criteria)} ACs well-formed. " + (
        f"issues={bad}" if bad else ""
    )
    return MetricResult(name="ac_testability", score=score, reason=reason.strip())
