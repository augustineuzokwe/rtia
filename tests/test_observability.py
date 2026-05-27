"""Tests for the LangSmith observability env-var contract.

These tests pin down exactly when tracing is considered ON. Wrong
detection is silently expensive - a CI run that thinks tracing is on but
isn't loses observability for that run; a local run that thinks it's off
when it isn't can leak data to the wrong LangSmith project.

Phase 12.4 adds the production guard: when ``RTIA_ENV=production``,
tracing is forcibly off and the pipeline refuses to start if both prod
and tracing-on are configured. See ``docs/adr-0008-pii-langsmith.md``.
"""

from __future__ import annotations

import pytest

from agents.observability import (
    ProductionTracingError,
    assert_safe_for_env,
    tracing_status,
)

LANGSMITH_ENV_VARS = ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT")
RTIA_ENV_VAR = "RTIA_ENV"


@pytest.fixture(autouse=True)
def _clear_langsmith_env(monkeypatch):
    """Ensure every test starts from an empty LangSmith env + unset RTIA_ENV."""
    for var in LANGSMITH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv(RTIA_ENV_VAR, raising=False)


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


# ---------------------------------------------------------------------------
# Phase 12.4 - production guard (ADR-0008)
# ---------------------------------------------------------------------------


def test_default_env_is_development():
    """When RTIA_ENV is unset, status reports the default env."""
    status = tracing_status()
    assert status.env == "development"


def test_dev_env_does_not_force_tracing_off(monkeypatch):
    """RTIA_ENV=development leaves the LangSmith vars as the source of truth."""
    monkeypatch.setenv("RTIA_ENV", "development")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")

    status = tracing_status()
    assert status.enabled is True
    assert status.env == "development"


def test_ci_env_does_not_force_tracing_off(monkeypatch):
    """RTIA_ENV=ci is treated like development for tracing - CI runs may trace."""
    monkeypatch.setenv("RTIA_ENV", "ci")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")

    status = tracing_status()
    assert status.enabled is True
    assert status.env == "ci"


def test_production_env_forces_tracing_off_even_when_configured(monkeypatch):
    """The defining 12.4 invariant: prod env disables tracing regardless of LangSmith vars."""
    monkeypatch.setenv("RTIA_ENV", "production")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")

    status = tracing_status()
    assert status.enabled is False
    assert status.env == "production"
    assert "ADR-0008" in status.reason


def test_production_env_with_tracing_off_is_silently_off(monkeypatch):
    """No warning noise when prod is correctly configured with tracing off."""
    monkeypatch.setenv("RTIA_ENV", "production")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    status = tracing_status()
    assert status.enabled is False
    assert status.env == "production"


def test_unrecognised_env_value_defaults_to_development(monkeypatch):
    """A typo like 'produciton' must NOT count as production for the guard.

    Treating a typo as production would be safer for *tracing* but the
    operator's misconfiguration there should surface via *some* other
    failure path; falling back to dev means tracing still works
    intentionally if the operator meant 'development'. The hard assert
    (assert_safe_for_env) only fires on the exact string 'production'.
    """
    monkeypatch.setenv("RTIA_ENV", "produciton")  # typo
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv_xyz")

    status = tracing_status()
    assert status.enabled is True
    assert status.env == "development"


def test_env_value_is_case_insensitive(monkeypatch):
    """RTIA_ENV is normalised to lowercase before matching the allowed set."""
    monkeypatch.setenv("RTIA_ENV", "PRODUCTION")

    status = tracing_status()
    assert status.env == "production"


# ---------------------------------------------------------------------------
# assert_safe_for_env - hard assert at process startup
# ---------------------------------------------------------------------------


def test_assert_safe_no_op_when_no_env_vars_set():
    """Unset RTIA_ENV + unset LANGSMITH_TRACING is the dev default - no raise."""
    assert_safe_for_env()  # should not raise


def test_assert_safe_no_op_in_development(monkeypatch):
    """Dev env never raises regardless of tracing flags."""
    monkeypatch.setenv("RTIA_ENV", "development")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert_safe_for_env()  # should not raise


def test_assert_safe_no_op_in_ci(monkeypatch):
    """CI env never raises regardless of tracing flags."""
    monkeypatch.setenv("RTIA_ENV", "ci")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert_safe_for_env()  # should not raise


def test_assert_safe_raises_on_prod_with_tracing_truthy(monkeypatch):
    """The defining failure mode - operator left LANGSMITH_TRACING on in prod."""
    monkeypatch.setenv("RTIA_ENV", "production")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(ProductionTracingError) as excinfo:
        assert_safe_for_env()
    message = str(excinfo.value)
    assert "production" in message.lower()
    assert "ADR-0008" in message or "adr-0008" in message.lower()


@pytest.mark.parametrize("truthy", ["true", "True", "1", "yes", "on"])
def test_assert_safe_raises_on_all_truthy_tracing_variants(monkeypatch, truthy):
    """Any truthy form of LANGSMITH_TRACING in prod triggers the abort."""
    monkeypatch.setenv("RTIA_ENV", "production")
    monkeypatch.setenv("LANGSMITH_TRACING", truthy)

    with pytest.raises(ProductionTracingError):
        assert_safe_for_env()


def test_assert_safe_no_op_in_prod_when_tracing_false(monkeypatch):
    """The correct prod config - no raise."""
    monkeypatch.setenv("RTIA_ENV", "production")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert_safe_for_env()  # should not raise


def test_assert_safe_raises_even_when_api_key_missing(monkeypatch):
    """LANGSMITH_TRACING=true without a key is still a misconfiguration in prod.

    The guard catches the operator's *intent* to trace (the truthy flag),
    not the *successful* trace. An incomplete config with the flag on
    fails loudly so the operator sees the conflict.
    """
    monkeypatch.setenv("RTIA_ENV", "production")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    # LANGSMITH_API_KEY intentionally unset.
    with pytest.raises(ProductionTracingError):
        assert_safe_for_env()


def test_assert_safe_no_op_on_typo_env_value(monkeypatch):
    """A typo'd RTIA_ENV does NOT count as production - falls back to dev."""
    monkeypatch.setenv("RTIA_ENV", "produciton")  # typo
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert_safe_for_env()  # should not raise - dev fallback


def test_status_line_shows_env_tag(monkeypatch):
    """The status line includes the env tag so the user sees which mode they're in."""
    monkeypatch.setenv("RTIA_ENV", "production")
    status = tracing_status()
    line = status.status_line()
    assert "[env: production]" in line
