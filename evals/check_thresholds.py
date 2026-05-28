"""CI eval gate: assert each metric's mean score meets its configured floor.

Reads an eval report JSON (produced by ``evals/run_evals.py``) and the
threshold config (``evals/thresholds.yaml``). Exits 0 if every gated
metric clears its floor; exits 1 with a clear per-metric failure list
otherwise.

Usage:
    uv run python evals/check_thresholds.py <report.json>
    uv run python evals/check_thresholds.py <report.json> --thresholds <path>

The script never invokes the LLM - it's pure post-processing on the
report. This means a failing CI run does not retry-and-burn cost; the
fix is to regenerate the report (or fix the agent).

Design choices:

- **Mean-across-samples gate, not per-sample.** Per-sample gating would
  amplify single-run variance - one stochastic dip on sample-02 would
  fail the gate even when the overall pipeline is healthy. Mean is the
  right granularity for the "did this PR regress?" question, matching
  the way baselines.md is written.
- **Missing metrics are an error**, not silently skipped. The thresholds
  file lists what we gate on; if the report lacks a gated metric, the
  pipeline silently dropped it and the gate should yell.
- **Unknown metrics in the report are ignored.** New metrics can be
  added to the runner before they're added to thresholds.yaml - useful
  for the "land the metric first, calibrate the floor in a follow-up PR"
  workflow that's already been used twice (, #102).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_THRESHOLDS = _REPO_ROOT / "evals" / "thresholds.yaml"


@dataclass(frozen=True)
class _GateResult:
    """Outcome of evaluating one metric against its floor."""

    name: str
    mean_score: float
    floor: float

    @property
    def passed(self) -> bool:
        return self.mean_score >= self.floor


def _load_thresholds(path: Path) -> dict[str, float]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    floors = data.get("metric_floors") or {}
    if not isinstance(floors, dict) or not floors:
        raise SystemExit(f"{path} has no 'metric_floors' map - the gate has nothing to enforce.")
    # Cast values up-front so a string-vs-float mistake fails loudly here,
    # not later inside the comparison.
    return {str(name): float(score) for name, score in floors.items()}


def _load_report(path: Path) -> dict[str, float]:
    """Return the report's per-metric mean scores.

    Pulls from ``aggregate.mean_scores`` because the runner already
    computes that - recomputing here would couple the gate to the
    per-sample structure and risk drift.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    aggregate = payload.get("aggregate") or {}
    means = aggregate.get("mean_scores") or {}
    if not means:
        raise SystemExit(
            f"{path} has no 'aggregate.mean_scores' - re-run evals/run_evals.py to "
            f"generate a current-format report."
        )
    return {str(name): float(score) for name, score in means.items()}


def evaluate(
    report_means: dict[str, float],
    floors: dict[str, float],
) -> list[_GateResult]:
    """Compute one _GateResult per gated metric.

    Missing metrics surface as a floor-vs-nan-style failure (mean_score
    set to -1 so the result clearly fails). Callers should print the
    full list - successes inform "what passed" telemetry, failures
    drive the non-zero exit.
    """
    results: list[_GateResult] = []
    for name, floor in floors.items():
        mean = report_means.get(name)
        if mean is None:
            # Use -1 as a sentinel that prints obviously wrong while still
            # being a float for the comparison.
            results.append(_GateResult(name=name, mean_score=-1.0, floor=floor))
        else:
            results.append(_GateResult(name=name, mean_score=mean, floor=floor))
    return results


def _format_results(results: list[_GateResult]) -> str:
    rows: list[str] = []
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        score_str = "MISSING" if r.mean_score < 0 else f"{r.mean_score:.2f}"
        rows.append(f"  [{marker}] {r.name:<28} mean={score_str:<7} floor={r.floor:.2f}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "report",
        type=Path,
        help="Path to an eval report JSON (output of evals/run_evals.py).",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=_DEFAULT_THRESHOLDS,
        help=f"Path to the thresholds YAML (default: {_DEFAULT_THRESHOLDS}).",
    )
    args = parser.parse_args(argv)

    floors = _load_thresholds(args.thresholds)
    means = _load_report(args.report)
    results = evaluate(means, floors)
    print(f"Eval gate: {args.report}")
    print(_format_results(results))

    failures = [r for r in results if not r.passed]
    if failures:
        print(
            f"\n{len(failures)} metric(s) below floor:\n"
            + "\n".join(
                f"  - {r.name}: mean={r.mean_score:.2f} < floor={r.floor:.2f}" for r in failures
            ),
            file=sys.stderr,
        )
        return 1
    print(f"\nAll {len(results)} gated metrics pass their floors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
