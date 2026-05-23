"""Tests for the per-agent duration baseline added in support of issue #163.

The eval report has long surfaced `pipeline_duration_ms` — the sum of every
production agent's wall-clock. To prioritise the pipeline-speedup work
(context caching vs. token caps vs. prompt compression), the runner now
also exposes a per-agent breakdown lifted from the same telemetry capture
that already feeds the token aggregates.

These tests guard the serialisation contract — what the JSON report and the
human-readable summary expose. Live latency numbers come from real eval runs
and aren't asserted here.
"""

from __future__ import annotations

from evals.run_evals import MetricResult, SampleReport, UsageTelemetry, _serialise


def _report(name: str, durations: dict[str, int], pipeline_ms: int) -> SampleReport:
    return SampleReport(
        name=name,
        analyst_output={},
        story_output={},
        ac_output={},
        tc_output={},
        metrics=[MetricResult(name="dummy", score=1.0, reason="")],
        analyst_usage=UsageTelemetry(),
        story_usage=UsageTelemetry(),
        ac_usage=UsageTelemetry(),
        tc_usage=UsageTelemetry(),
        pipeline_duration_ms=pipeline_ms,
        per_agent_duration_ms=dict(durations),
    )


def test_serialise_includes_per_sample_per_agent_durations():
    durations = {
        "requirements_analyst": 4000,
        "user_story_writer": 5000,
        "ac_generator": 6000,
        "test_case_writer": 7000,
    }
    report = _report("sample-01", durations, sum(durations.values()))

    payload = _serialise([report], judge_model="gemini-3.5-flash")

    sample_payload = payload["samples"][0]
    assert sample_payload["per_agent_duration_ms"] == durations
    # Round-trip didn't drop the existing aggregate.
    assert sample_payload["pipeline_duration_ms"] == sum(durations.values())


def test_serialise_aggregates_per_agent_durations_across_samples():
    r1 = _report(
        "sample-01",
        {"requirements_analyst": 1000, "user_story_writer": 2000},
        3000,
    )
    r2 = _report(
        "sample-02",
        {"requirements_analyst": 1500, "ac_generator": 3500},
        5000,
    )

    payload = _serialise([r1, r2], judge_model="gemini-3.5-flash")

    aggregate = payload["aggregate"]["per_agent_duration_ms"]
    assert aggregate == {
        "requirements_analyst": 2500,
        "user_story_writer": 2000,
        "ac_generator": 3500,
    }
    # Aggregate pipeline duration unchanged by the new field.
    assert payload["aggregate"]["pipeline_duration_ms"] == 8000


def test_serialise_handles_missing_capture_gracefully():
    """If capture is unavailable, the per-agent dict is empty but the
    schema is still present so downstream readers don't need optional
    handling."""
    report = _report("sample-01", {}, 0)

    payload = _serialise([report], judge_model="gemini-3.5-flash")

    assert payload["samples"][0]["per_agent_duration_ms"] == {}
    assert payload["aggregate"]["per_agent_duration_ms"] == {}
