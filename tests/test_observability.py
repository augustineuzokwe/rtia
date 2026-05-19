"""Tests for the LangSmith observability env-var contract.

These tests pin down exactly when tracing is considered ON. Wrong
detection is silently expensive — a CI run that thinks tracing is on but
isn't loses observability for that run; a local run that thinks it's off
when it isn't can leak data to the wrong LangSmith project.
"""

from __future__ import annotations

import pytest

from agents.observability import tracing_status

LANGSMITH_ENV_VARS = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT")


@pytest.fixture(autouse=True)
def _clear_langsmith_env(monkeypatch):
    """Ensure every test starts from an empty LangSmith env."""
    for var in LANGSMITH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_tracing_off_when_no_env_vars_set():
    status = tracing_status()
    assert status.enabled is False
    assert "not set" in status.reason


def test_tracing_on_when_flag_and_key_present(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")
    monkeypatch.setenv("LANGSMITH_PROJECT", "rtia")

    status = tracing_status()
    assert status.enabled is True
    assert status.project == "rtia"


def test_flag_truthy_variants_all_enable_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")
    for truthy in ("true", "True", "1", "yes", "on"):
        monkeypatch.setenv("LANGSMITH_TRACING", truthy)
        assert tracing_status().enabled is True, f"value {truthy!r} should enable"


def test_flag_set_but_key_missing_reports_helpful_reason(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    status = tracing_status()
    assert status.enabled is False
    assert "LANGSMITH_API_KEY is empty" in status.reason


def test_key_set_but_flag_missing_reports_helpful_reason(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")

    status = tracing_status()
    assert status.enabled is False
    assert "LANGSMITH_TRACING is not 'true'" in status.reason


def test_falsy_flag_values_keep_tracing_off(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")
    for falsy in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("LANGSMITH_TRACING", falsy)
        assert tracing_status().enabled is False, f"value {falsy!r} should not enable"
