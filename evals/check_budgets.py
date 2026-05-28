"""CI cost + latency gate.

Reads an eval report JSON (produced by ``evals/run_evals.py``) and the
``[tool.rtia.budgets]`` block in ``pyproject.toml``. Exits 0 if every
budget is respected; exits 1 with a clear per-budget failure list
otherwise.

Usage:
    uv run python evals/check_budgets.py <report.json>
    uv run python evals/check_budgets.py <report.json> --pyproject <path>

Sibling of ``evals/check_thresholds.py`` - same post-processing pattern,
same "don't burn LLM cost on a regenerate" rule. A failing budget gate
means either (a) a real regression worth investigating or (b) the
budget is stale and should be retuned per evals/baselines.md's
calibration workflow.

Why this lives separately from check_thresholds.py:

* Different signal - quality regression (thresholds) vs cost/latency
  regression (budgets). Bundling them would force a single failure
  message even when one is the cause and the other is fine.
* Different audit trail - budgets get tightened gradually as the
  pipeline matures; thresholds get ratcheted in baseline-update PRs.
  Keeping them apart makes each PR's intent legible.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PYPROJECT = _REPO_ROOT / "pyproject.toml"


@dataclass(frozen=True)
class _BudgetCheck:
    """Outcome of evaluating one budget."""

    name: str
    """Human-readable budget name (e.g. 'per_sample_total_tokens')."""
    observed: int | float
    limit: int | float
    unit: str
    """Display unit ('tokens', 'seconds') - used only in error messages."""
    sample: str | None
    """Sample name when this is a per-sample check; None for aggregates."""

    @property
    def passed(self) -> bool:
        return self.observed <= self.limit

    def format(self) -> str:
        location = f" [{self.sample}]" if self.sample else ""
        marker = "ok" if self.passed else "OVER"
        return (
            f"  {marker:<4} {self.name}{location}: {self.observed} {self.unit} (limit {self.limit})"
        )


def _load_budgets(pyproject_path: Path) -> dict:
    """Read the ``[tool.rtia.budgets]`` block from pyproject.toml."""
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    try:
        return data["tool"]["rtia"]["budgets"]
    except KeyError as exc:
        raise SystemExit(
            f"No [tool.rtia.budgets] block found in {pyproject_path}. "
            "Phase 13.1 budgets must be declared before this gate runs."
        ) from exc


def _check_budgets(report: dict, budgets: dict) -> list[_BudgetCheck]:
    """Apply each configured budget against the report."""
    checks: list[_BudgetCheck] = []

    per_sample_tokens_limit = budgets["per_sample_total_tokens_max"]
    per_sample_duration_limit_seconds = budgets["per_sample_pipeline_duration_seconds_max"]
    total_tokens_limit = budgets["total_tokens_max"]
    total_duration_limit_seconds = budgets["total_pipeline_duration_seconds_max"]

    # Per-sample checks - catches a single outlier that doesn't move the aggregate.
    for sample in report.get("samples", []):
        pipeline_usage = sample.get("pipeline_usage", {})
        sample_tokens = int(pipeline_usage.get("input_tokens", 0)) + int(
            pipeline_usage.get("output_tokens", 0)
        )
        sample_duration_ms = int(sample.get("pipeline_duration_ms", 0))
        checks.append(
            _BudgetCheck(
                name="per_sample_total_tokens",
                observed=sample_tokens,
                limit=per_sample_tokens_limit,
                unit="tokens",
                sample=sample.get("name"),
            )
        )
        checks.append(
            _BudgetCheck(
                name="per_sample_pipeline_duration",
                observed=round(sample_duration_ms / 1000.0, 1),
                limit=per_sample_duration_limit_seconds,
                unit="seconds",
                sample=sample.get("name"),
            )
        )

    # Aggregate checks - catches uniform drift across all samples.
    aggregate = report.get("aggregate", {})
    pipeline_usage = aggregate.get("pipeline_usage", {})
    total_tokens = int(pipeline_usage.get("input_tokens", 0)) + int(
        pipeline_usage.get("output_tokens", 0)
    )
    total_duration_ms = int(aggregate.get("pipeline_duration_ms", 0))
    checks.append(
        _BudgetCheck(
            name="total_tokens",
            observed=total_tokens,
            limit=total_tokens_limit,
            unit="tokens",
            sample=None,
        )
    )
    checks.append(
        _BudgetCheck(
            name="total_pipeline_duration",
            observed=round(total_duration_ms / 1000.0, 1),
            limit=total_duration_limit_seconds,
            unit="seconds",
            sample=None,
        )
    )

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_path", type=Path, help="Path to eval report JSON.")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=_DEFAULT_PYPROJECT,
        help="Path to pyproject.toml (default: repo root).",
    )
    args = parser.parse_args(argv)

    report = json.loads(args.report_path.read_text(encoding="utf-8"))
    budgets = _load_budgets(args.pyproject)
    checks = _check_budgets(report, budgets)

    failed = [c for c in checks if not c.passed]
    if not failed:
        print(f"Budgets - all {len(checks)} checks within limits:")
        for c in checks:
            print(c.format())
        return 0

    # Print everything; failures stand out via the OVER marker. The full
    # list makes it obvious whether a single outlier or a fleet drift
    # is the problem.
    print(f"Budgets - {len(failed)} of {len(checks)} checks OVER limit:", file=sys.stderr)
    for c in checks:
        print(c.format(), file=sys.stderr)
    print(
        "\nFailing budgets are real signals. Investigate the regression "
        "(prompt changes? new agent steps? upstream cost shift?) or, if "
        "the ceiling is stale, retune in [tool.rtia.budgets] in pyproject.toml "
        "with a justification line in the PR body.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
