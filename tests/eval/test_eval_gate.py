"""Tests for the CI eval gate (evals/check_thresholds.py).

No live LLM calls - the gate is pure post-processing on a report JSON.
Tests cover the contract:

- All metrics pass → exit 0.
- Any metric below floor → exit 1.
- A gated metric missing from the report → exit 1 (silent drops yell).
- A metric in the report but NOT in thresholds → ignored (forward-compat
  for landing a metric before its floor is calibrated).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from evals.check_thresholds import evaluate, main


def _write_report(tmp_path: Path, mean_scores: dict[str, float]) -> Path:
    payload = {
        "aggregate": {"mean_scores": mean_scores},
        "samples": [],
    }
    p = tmp_path / "report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _write_thresholds(tmp_path: Path, floors: dict[str, float]) -> Path:
    p = tmp_path / "thresholds.yaml"
    p.write_text(yaml.safe_dump({"metric_floors": floors}), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# evaluate() - pure function, no I/O
# ---------------------------------------------------------------------------


def test_evaluate_all_metrics_pass() -> None:
    results = evaluate(
        {"actor_set_completeness": 0.87, "ac_coverage": 0.90},
        {"actor_set_completeness": 0.70, "ac_coverage": 0.80},
    )
    assert all(r.passed for r in results)


def test_evaluate_metric_below_floor_fails() -> None:
    results = evaluate(
        {"ambiguity_discipline": 0.40},
        {"ambiguity_discipline": 0.50},
    )
    assert not results[0].passed
    assert results[0].mean_score == 0.40
    assert results[0].floor == 0.50


def test_evaluate_missing_metric_fails() -> None:
    """A gated metric absent from the report MUST fail the gate, not silently pass."""
    results = evaluate(
        {"ac_coverage": 0.90},
        {"actor_set_completeness": 0.70, "ac_coverage": 0.80},
    )
    by_name = {r.name: r for r in results}
    assert not by_name["actor_set_completeness"].passed
    assert by_name["actor_set_completeness"].mean_score < 0  # sentinel
    assert by_name["ac_coverage"].passed


def test_evaluate_extra_metric_in_report_ignored() -> None:
    """Metrics in the report but not in thresholds are NOT gated (forward-compat)."""
    results = evaluate(
        {"actor_set_completeness": 0.87, "future_metric": 0.10},
        {"actor_set_completeness": 0.70},
    )
    names = [r.name for r in results]
    assert names == ["actor_set_completeness"]


def test_evaluate_exact_floor_passes() -> None:
    """A score equal to the floor passes (>=, not >)."""
    results = evaluate({"ac_testability": 0.80}, {"ac_testability": 0.80})
    assert results[0].passed


# ---------------------------------------------------------------------------
# main() - exit codes via the CLI entry point
# ---------------------------------------------------------------------------


def test_main_returns_zero_when_all_pass(tmp_path: Path) -> None:
    report = _write_report(tmp_path, {"ac_coverage": 0.90, "ac_testability": 1.00})
    thresholds = _write_thresholds(tmp_path, {"ac_coverage": 0.80, "ac_testability": 0.80})
    assert main([str(report), "--thresholds", str(thresholds)]) == 0


def test_main_returns_one_on_failure(tmp_path: Path) -> None:
    report = _write_report(tmp_path, {"ac_coverage": 0.40})
    thresholds = _write_thresholds(tmp_path, {"ac_coverage": 0.80})
    assert main([str(report), "--thresholds", str(thresholds)]) == 1


def test_main_returns_one_on_missing_metric(tmp_path: Path) -> None:
    report = _write_report(tmp_path, {"ac_coverage": 0.90})  # ac_testability missing
    thresholds = _write_thresholds(tmp_path, {"ac_coverage": 0.80, "ac_testability": 0.80})
    assert main([str(report), "--thresholds", str(thresholds)]) == 1


def test_main_errors_on_empty_thresholds(tmp_path: Path) -> None:
    report = _write_report(tmp_path, {"ac_coverage": 0.90})
    bad = tmp_path / "thresholds.yaml"
    bad.write_text("metric_floors: {}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main([str(report), "--thresholds", str(bad)])


def test_main_errors_on_report_without_aggregate(tmp_path: Path) -> None:
    bad_report = tmp_path / "report.json"
    bad_report.write_text(json.dumps({"samples": []}), encoding="utf-8")
    thresholds = _write_thresholds(tmp_path, {"ac_coverage": 0.80})
    with pytest.raises(SystemExit):
        main([str(bad_report), "--thresholds", str(thresholds)])
