"""Tests for the Phase 13.1 cost + latency budget gate.

Covers two surfaces:

1. ``evals/check_budgets.py`` — the post-processing gate against a
   minimal synthetic report. Confirms ok/over-limit detection,
   per-sample vs aggregate scoping, and the exit-code contract.
2. ``evals/_telemetry_capture.py`` — the logging-handler-based bag that
   collects ``agent_invocation_end`` records. Confirms it picks up
   real records emitted by ``log_agent_invocation`` and that the
   handler is removed cleanly on exit (no leakage into subsequent tests).
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path

import pytest

from agents._logging import LOGGER_NAMESPACE, log_agent_invocation
from evals._telemetry_capture import (
    AgentInvocationObservation,
    TelemetryCapture,
    capture_agent_telemetry,
)
from evals.check_budgets import main as check_budgets_main

# ---------------------------------------------------------------------------
# Telemetry capture
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, input_t: int, output_t: int, total_t: int):
        self.usage_metadata = {
            "input_tokens": input_t,
            "output_tokens": output_t,
            "total_tokens": total_t,
        }


def test_capture_collects_agent_invocation_end_records():
    with capture_agent_telemetry() as telemetry:
        with log_agent_invocation("requirements_analyst") as rec:
            rec.record_response(_FakeResponse(100, 50, 150))
        with log_agent_invocation("ac_generator") as rec:
            rec.record_response(_FakeResponse(80, 40, 120))

    assert len(telemetry.observations) == 2
    analyst = telemetry.for_agent("requirements_analyst")
    assert analyst is not None
    assert analyst.input_tokens == 100
    assert analyst.output_tokens == 50
    assert analyst.total_tokens == 150
    assert analyst.duration_ms >= 0
    assert telemetry.total_input_tokens == 180
    assert telemetry.total_output_tokens == 90
    assert telemetry.total_tokens == 270


def test_capture_total_tokens_falls_back_to_sum_when_total_missing():
    """If usage_metadata lacks 'total_tokens', sum input+output."""

    class _NoTotal:
        usage_metadata = {"input_tokens": 7, "output_tokens": 3}

    with capture_agent_telemetry() as telemetry, log_agent_invocation("reviewer") as rec:
        rec.record_response(_NoTotal())

    assert telemetry.total_tokens == 10


def test_capture_ignores_unrelated_log_records():
    """Records that aren't agent_invocation_end must not leak in."""
    logger = logging.getLogger(LOGGER_NAMESPACE)
    with capture_agent_telemetry() as telemetry:
        logger.info("random message", extra={"event": "boot"})
    assert telemetry.observations == []


def test_capture_handler_removed_on_exit():
    """No leak: the rtia logger has the same handler count after exit."""
    logger = logging.getLogger(LOGGER_NAMESPACE)
    before = len(logger.handlers)
    with capture_agent_telemetry():
        assert len(logger.handlers) == before + 1
    assert len(logger.handlers) == before


def test_capture_nested_contexts_each_get_their_own_bag():
    """Two concurrent captures don't share observations."""
    with capture_agent_telemetry() as outer:
        with log_agent_invocation("requirements_analyst") as rec:
            rec.record_response(_FakeResponse(1, 1, 2))
        with capture_agent_telemetry() as inner:
            with log_agent_invocation("ac_generator") as rec:
                rec.record_response(_FakeResponse(10, 10, 20))
            # Inner sees only the inner record.
            assert [o.agent for o in inner.observations] == ["ac_generator"]
        # Outer sees both because the inner handler was attached on top.
        assert [o.agent for o in outer.observations] == [
            "requirements_analyst",
            "ac_generator",
        ]


# ---------------------------------------------------------------------------
# check_budgets.py — gate behaviour against synthetic reports
# ---------------------------------------------------------------------------

_GENEROUS_PYPROJECT = """\
[tool.rtia.budgets]
per_sample_total_tokens_max = 50000
per_sample_pipeline_duration_seconds_max = 600
total_tokens_max = 200000
total_pipeline_duration_seconds_max = 1800
"""

_TIGHT_PYPROJECT = """\
[tool.rtia.budgets]
per_sample_total_tokens_max = 100
per_sample_pipeline_duration_seconds_max = 1
total_tokens_max = 100
total_pipeline_duration_seconds_max = 1
"""


def _write_report(tmp_path: Path) -> Path:
    """Write a synthetic eval report with two samples + an aggregate."""
    report = {
        "samples": [
            {
                "name": "sample-01",
                "pipeline_usage": {"input_tokens": 2000, "output_tokens": 1000},
                "pipeline_duration_ms": 30000,
            },
            {
                "name": "sample-02",
                "pipeline_usage": {"input_tokens": 1500, "output_tokens": 1500},
                "pipeline_duration_ms": 45000,
            },
        ],
        "aggregate": {
            "pipeline_usage": {"input_tokens": 3500, "output_tokens": 2500},
            "pipeline_duration_ms": 75000,
        },
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _write_pyproject(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_check_budgets_passes_when_under_all_limits(tmp_path, capsys):
    report = _write_report(tmp_path)
    pyproject = _write_pyproject(tmp_path, _GENEROUS_PYPROJECT)
    rc = check_budgets_main([str(report), "--pyproject", str(pyproject)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "all" in out and "within limits" in out


def test_check_budgets_fails_when_per_sample_over(tmp_path, capsys):
    report = _write_report(tmp_path)
    pyproject = _write_pyproject(tmp_path, _TIGHT_PYPROJECT)
    rc = check_budgets_main([str(report), "--pyproject", str(pyproject)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "OVER" in err
    # Per-sample check identifies which sample blew the budget.
    assert "sample-01" in err
    assert "sample-02" in err


def test_check_budgets_fails_when_aggregate_over_but_per_sample_ok(tmp_path, capsys):
    """Uniform drift: each sample fits, but the aggregate doesn't."""
    pyproject_content = """\
    [tool.rtia.budgets]
    per_sample_total_tokens_max = 5000
    per_sample_pipeline_duration_seconds_max = 600
    total_tokens_max = 1000
    total_pipeline_duration_seconds_max = 1800
    """
    report = _write_report(tmp_path)
    pyproject = _write_pyproject(tmp_path, pyproject_content)
    rc = check_budgets_main([str(report), "--pyproject", str(pyproject)])
    assert rc == 1
    err = capsys.readouterr().err
    # The aggregate line in output has no [sample-XX] suffix.
    aggregate_lines = [line for line in err.splitlines() if "total_tokens" in line]
    assert any("OVER" in line for line in aggregate_lines)


def test_check_budgets_raises_when_pyproject_lacks_budgets(tmp_path):
    report = _write_report(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        check_budgets_main([str(report), "--pyproject", str(pyproject)])


# ---------------------------------------------------------------------------
# Direct API — TelemetryCapture aggregate properties
# ---------------------------------------------------------------------------


def test_aggregate_properties_handle_missing_token_counts():
    bag = TelemetryCapture(
        observations=[
            AgentInvocationObservation(
                agent="a",
                status="ok",
                duration_ms=100,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
            ),
            AgentInvocationObservation(
                agent="b",
                status="ok",
                duration_ms=200,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        ]
    )
    assert bag.total_input_tokens == 10  # None treated as 0 for sum
    assert bag.total_output_tokens == 5
    assert bag.total_tokens == 15
    assert bag.total_duration_ms == 300
