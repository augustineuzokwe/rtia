"""Test-Case-layer metrics for the per-agent eval suite.

Two metrics, each scored in [0.0, 1.0]. Both are fully programmatic — no
LLM judge calls, no API spend. Mirrors the ``ac_testability`` pattern
established in ``evals/ac_metrics.py``.

- ``tc_coverage_breadth`` — averages two sub-scores: (a) whether all
  three coverage types (``happy_path``, ``edge_case``, ``negative``) are
  represented across the generated test cases, and (b) the fraction of
  the story's acceptance criteria that have at least one test case
  derived from them, measured via token overlap between the AC's ``then``
  clause and a test case's ``expected`` clause.
- ``tc_executability`` — fraction of test cases whose ``steps`` are free
  of vague-placeholder patterns (``<value>``, weasel words like ``some``,
  ``appropriate``, ``valid``, ``relevant``). Identifies the failure mode
  where the model writes ``Enter <value> in the field`` instead of a
  concrete fixture.

Metric API mirrors ``evals/metrics.py`` and ``evals/ac_metrics.py``:
each function returns a ``MetricResult`` (name + score + reason).
"""

from __future__ import annotations

import re

from agents.final_artifact import AcceptanceCriterion, TestCase
from evals._text_utils import token_overlap
from evals.metrics import MetricResult

# ---------------------------------------------------------------------------
# tc_coverage_breadth
# ---------------------------------------------------------------------------

_REQUIRED_TYPES: frozenset[str] = frozenset({"happy_path", "edge_case", "negative"})

# Threshold for the AC→TC token-overlap heuristic. 0.3 = 30% of the AC's
# meaningful tokens must appear in the test case's `expected` clause for
# the AC to count as "covered". Tunable; start permissive and tighten if
# the metric proves too easy to satisfy on real runs.
_AC_TC_OVERLAP_THRESHOLD = 0.3


def score_tc_coverage_breadth(
    test_cases: list[TestCase],
    acceptance_criteria: list[AcceptanceCriterion],
) -> MetricResult:
    """Average of coverage-type breadth and AC-coverage breadth.

    Edge cases:
    - No test cases generated: score 0.0 (the agent produced nothing to
      grade — vacuous "covered" is the wrong answer).
    - No acceptance criteria provided: AC-coverage sub-score defaults to
      1.0 (nothing to cover); the type-coverage sub-score still applies.
    """
    if not test_cases:
        return MetricResult(
            name="tc_coverage_breadth",
            score=0.0,
            reason="No test cases to score.",
        )

    types_present = {tc.type for tc in test_cases}
    type_score = len(types_present & _REQUIRED_TYPES) / len(_REQUIRED_TYPES)

    if acceptance_criteria:
        covered_acs = sum(
            1
            for ac in acceptance_criteria
            if any(
                token_overlap(ac.then, tc.expected) >= _AC_TC_OVERLAP_THRESHOLD for tc in test_cases
            )
        )
        ac_score = covered_acs / len(acceptance_criteria)
        ac_detail = (
            f"{covered_acs}/{len(acceptance_criteria)} ACs covered "
            f"(overlap≥{_AC_TC_OVERLAP_THRESHOLD})"
        )
    else:
        ac_score = 1.0
        ac_detail = "no ACs to cover"

    score = (type_score + ac_score) / 2
    missing_types = sorted(_REQUIRED_TYPES - types_present)
    reason = (
        f"type_score={type_score:.2f} ({sorted(types_present & _REQUIRED_TYPES)} present"
        f"{f', missing={missing_types}' if missing_types else ''}); "
        f"ac_score={ac_score:.2f} ({ac_detail}); "
        f"mean={score:.2f}."
    )
    return MetricResult(name="tc_coverage_breadth", score=score, reason=reason)


# ---------------------------------------------------------------------------
# tc_executability
# ---------------------------------------------------------------------------

# Patterns that signal a step is not concrete enough to execute cold.
# Hand-curated; expand only with concrete evidence from real eval runs,
# not speculatively. The cost per violation (0.25) means a step with up
# to 3 distinct violations scores 0.25; 4+ violations score 0.
_VAGUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Angle-bracket placeholders: "<value>", "<URL>", "<username>".
    re.compile(r"<[^>]+>"),
    # Weasel words signalling unspecified content.
    re.compile(r"\b(some|appropriate|valid|relevant)\b", re.IGNORECASE),
)

_PER_VIOLATION_PENALTY = 0.25


def _executability_violations(test_case: TestCase) -> list[str]:
    joined = " ".join(test_case.steps)
    hits: list[str] = []
    for pattern in _VAGUE_PATTERNS:
        match = pattern.search(joined)
        if match:
            hits.append(match.group(0))
    return hits


def score_tc_executability(test_cases: list[TestCase]) -> MetricResult:
    """Mean per-test-case executability score.

    Each test case starts at 1.0 and loses ``_PER_VIOLATION_PENALTY`` for
    each distinct vague-pattern hit across all its steps. Floor at 0.

    The metric is *not* a hard pass/fail — a single ``<value>`` in one
    step drops that test case to 0.75 rather than 0, so the score
    degrades smoothly. This matches the pedagogy in baselines.md (most
    AC/TC metrics are continuous fractions, not boolean gates).
    """
    if not test_cases:
        return MetricResult(
            name="tc_executability",
            score=0.0,
            reason="No test cases to score.",
        )

    case_scores: list[float] = []
    issues: list[str] = []
    for i, tc in enumerate(test_cases):
        hits = _executability_violations(tc)
        case_score = max(0.0, 1.0 - _PER_VIOLATION_PENALTY * len(hits))
        case_scores.append(case_score)
        if hits:
            issues.append(f"TC#{i} ({tc.scenario!r}): {hits}")

    mean = sum(case_scores) / len(case_scores)
    reason = (
        f"{sum(1 for s in case_scores if s == 1.0)}/{len(case_scores)} test cases fully concrete; "
        f"mean={mean:.2f}. " + (f"issues={issues}" if issues else "")
    )
    return MetricResult(name="tc_executability", score=mean, reason=reason.strip())
