"""Tests for ``evals/n_runs.py`` — stochastic AC validation (Issue #233).

These assertions defend the design that prevents the adversarial-tail
false-green trap:

- N > 1 with cache enabled is a hard error (the N draws would collapse
  to 1 cached measurement repeated N times).
- The pass-rate computation correctly counts runs at-or-above floor.
- A sample with a known 80% success rate fails at threshold=95% and
  passes at threshold=70%.
- The adversarial / non-adversarial sample classifier matches the
  per-sample matrix in [ADR-0014](../docs/adr-0014-stochastic-ac-validation.md).
- The JSON report shape distinguishes itself from the single-run report
  (top-level ``samples[].per_metric[].pass_rate`` vs the single-run
  ``aggregate.mean_scores``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.n_runs import (
    ADVERSARIAL_SAMPLE_PREFIXES,
    NRunMetricResult,
    NRunSampleReport,
    assert_cache_disabled_for_n_runs,
    is_adversarial_sample,
    load_metric_floors,
    serialise_n_run_reports,
    write_n_run_report,
)


class TestAdversarialClassifier:
    @pytest.mark.parametrize(
        "name",
        ["sample-04-injection-suffix", "sample-05-x", "sample-06-y", "sample-07-z"],
    )
    def test_adversarial_samples_detected(self, name: str) -> None:
        assert is_adversarial_sample(name)

    @pytest.mark.parametrize(
        "name", ["sample-01-well-structured", "sample-02-vague", "sample-03-multi"]
    )
    def test_non_adversarial_samples_detected(self, name: str) -> None:
        assert not is_adversarial_sample(name)

    def test_adversarial_prefixes_match_adr_0014_matrix(self) -> None:
        """ADR-0014's per-sample matrix lists 04-07 as adversarial. Lock that in."""
        assert ADVERSARIAL_SAMPLE_PREFIXES == ("sample-04", "sample-05", "sample-06", "sample-07")


class TestCacheDisableInvariant:
    def test_n_runs_eq_1_does_not_check_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # N=1 is backward-compat — the assertion must not fire.
        monkeypatch.setenv("RTIA_LLM_CACHE", "enabled")
        assert_cache_disabled_for_n_runs(1)  # no raise

    def test_n_runs_gt_1_with_cache_enabled_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RTIA_LLM_CACHE", "enabled")
        with pytest.raises(RuntimeError, match="RTIA_LLM_CACHE=disabled"):
            assert_cache_disabled_for_n_runs(10)

    def test_n_runs_gt_1_with_cache_disabled_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RTIA_LLM_CACHE", "disabled")
        assert_cache_disabled_for_n_runs(10)  # no raise

    def test_default_env_treated_as_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTIA_LLM_CACHE", raising=False)
        with pytest.raises(RuntimeError):
            assert_cache_disabled_for_n_runs(10)


class TestPassRateAggregation:
    def test_all_passing_yields_pass_rate_one(self) -> None:
        r = NRunMetricResult.aggregate("m", scores=[1.0, 1.0, 1.0, 1.0], floor=0.8)
        assert r.pass_rate == 1.0

    def test_eighty_percent_passing(self) -> None:
        # 8 of 10 runs >= 0.80 floor; 2 below.
        scores = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.5, 0.5]
        r = NRunMetricResult.aggregate("m", scores=scores, floor=0.8)
        assert r.pass_rate == pytest.approx(0.8)
        assert r.min_score == 0.5
        assert r.max_score == 0.9

    def test_exact_floor_counts_as_pass(self) -> None:
        # Floor is inclusive: score == floor passes.
        r = NRunMetricResult.aggregate("m", scores=[0.8, 0.8, 0.8], floor=0.8)
        assert r.pass_rate == 1.0

    def test_empty_scores_raises(self) -> None:
        with pytest.raises(ValueError, match="empty score list"):
            NRunMetricResult.aggregate("m", scores=[], floor=0.5)


class TestSampleReportPassFail:
    def _adversarial_metric(self, scores: list[float]) -> NRunMetricResult:
        return NRunMetricResult.aggregate("injection_resistance", scores=scores, floor=1.0)

    def test_80pct_pass_rate_fails_at_threshold_95(self) -> None:
        # 8/10 runs pass the floor — sample fails at threshold=0.95.
        scores = [1.0] * 8 + [0.5] * 2
        report = NRunSampleReport(
            sample_name="sample-05-injection-inline",
            n_runs=10,
            threshold=0.95,
            is_adversarial=True,
            per_metric=[self._adversarial_metric(scores)],
        )
        assert not report.passed
        assert "injection_resistance" in report.failing_metrics()

    def test_80pct_pass_rate_passes_at_threshold_70(self) -> None:
        scores = [1.0] * 8 + [0.5] * 2
        report = NRunSampleReport(
            sample_name="sample-05-injection-inline",
            n_runs=10,
            threshold=0.70,
            is_adversarial=True,
            per_metric=[self._adversarial_metric(scores)],
        )
        assert report.passed
        assert report.failing_metrics() == []

    def test_empty_metrics_is_failed(self) -> None:
        report = NRunSampleReport(
            sample_name="x", n_runs=10, threshold=0.95, is_adversarial=True, per_metric=[]
        )
        assert not report.passed


class TestReportSerialisation:
    def test_payload_shape_distinguishes_from_single_run(self, tmp_path: Path) -> None:
        m = NRunMetricResult.aggregate("metric_a", scores=[1.0, 1.0, 0.0], floor=0.8)
        r = NRunSampleReport(
            sample_name="sample-05-x",
            n_runs=3,
            threshold=0.95,
            is_adversarial=True,
            per_metric=[m],
        )
        payload = serialise_n_run_reports([r], judge_model="gemini-3.5-flash")
        # Top-level keys that announce "this is an N-run report":
        assert "n_runs" in payload
        assert payload["n_runs"] == 3
        # Per-sample shape carries pass_rate, not mean_scores:
        sample_payload = payload["samples"][0]
        assert "per_metric" in sample_payload
        per_metric = sample_payload["per_metric"][0]
        assert "pass_rate" in per_metric
        assert "floor" in per_metric
        assert "scores" in per_metric  # raw per-run scores preserved
        # Summary section reports pass/fail aggregate.
        summary = payload["summary"]
        assert summary["samples_passed"] == 0  # 2/3 < 95% threshold
        assert summary["samples_failed"] == 1

    def test_write_n_run_report_persists(self, tmp_path: Path) -> None:
        payload = {"n_runs": 5, "samples": [], "summary": {}}
        out = tmp_path / "n-run-test.json"
        write_n_run_report(payload, out_path=out)
        assert out.exists()
        roundtrip = json.loads(out.read_text(encoding="utf-8"))
        assert roundtrip == payload


class TestFloorLoading:
    def test_load_floors_returns_expected_metric_names(self) -> None:
        # Snapshot-style assertion against the live thresholds.yaml — if the
        # file gains or loses a metric, this test surfaces the change so the
        # ADR-0014 contract can be re-checked.
        floors = load_metric_floors()
        assert "ac_coverage" in floors
        assert "injection_resistance" in floors
        # Floor values must be floats in [0.0, 1.0].
        for name, floor in floors.items():
            assert 0.0 <= floor <= 1.0, f"{name} has out-of-range floor {floor}"


class TestNightlyWorkflowContract:
    """Lock in the contract between ADR-0014 and the nightly workflow."""

    def test_nightly_workflow_uses_n_runs_and_no_cache(self) -> None:
        import yaml

        wf_path = (
            Path(__file__).resolve().parent.parent
            / ".github"
            / "workflows"
            / "nightly-safety-regression.yml"
        )
        assert wf_path.exists(), "Nightly workflow file missing"
        workflow = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
        job = workflow["jobs"]["stochastic-adversarial"]
        # Find the step that runs evals.
        run_step = next(
            step
            for step in job["steps"]
            if isinstance(step, dict) and "run" in step and "run_evals" in step["run"]
        )
        run_body = run_step["run"]
        # Both invariants from ADR-0014 must appear.
        assert "--no-cache" in run_body
        assert "--n-runs" in run_body
        # Env-level disable too (belt-and-suspenders).
        env = run_step.get("env") or {}
        assert env.get("RTIA_LLM_CACHE") == "disabled"
