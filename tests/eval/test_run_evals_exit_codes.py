"""Tests for the CI exit-code classifier in ``evals/run_evals.py``.

The CI workflow's ``nick-fields/retry@v4`` step is configured with
``retry_on_exit_code: 76`` so only a transient Gemini 503 retries; a
Gemini 429 (project spend cap) fails fast on the first attempt. This
test pins the classifier contract that wires that up - if the exit
codes drift, the CI retry behaviour silently regresses. Issue #329.
"""

from __future__ import annotations

from evals.run_evals import (
    _EXIT_GEMINI_429,
    _EXIT_GEMINI_TRANSIENT,
    _append_step_summary,
    _classify_exit_code,
    _extract_http_status,
)


class _FakeDetail:
    def __init__(self, http_status: int) -> None:
        self.http_status = http_status


class _FakeLLMPipelineError(Exception):
    def __init__(self, http_status: int) -> None:
        super().__init__(f"fake LLM error status={http_status}")
        self.detail = _FakeDetail(http_status)


class _FakeAPIError(Exception):
    """Stand-in for ``google.genai.errors.APIError`` - exposes ``.code``."""

    def __init__(self, code: int) -> None:
        super().__init__(f"fake API error code={code}")
        self.code = code


def test_classify_503_returns_retry_exit_code():
    assert _classify_exit_code(_FakeLLMPipelineError(503)) == _EXIT_GEMINI_TRANSIENT


def test_classify_504_returns_retry_exit_code():
    """504 DEADLINE_EXCEEDED observed live on PR #330 first run - same class
    of transient Gemini failure as 503."""
    assert _classify_exit_code(_FakeLLMPipelineError(504)) == _EXIT_GEMINI_TRANSIENT


def test_classify_502_returns_retry_exit_code():
    assert _classify_exit_code(_FakeLLMPipelineError(502)) == _EXIT_GEMINI_TRANSIENT


def test_classify_429_returns_fail_fast_exit_code():
    assert _classify_exit_code(_FakeLLMPipelineError(429)) == _EXIT_GEMINI_429


def test_classify_raw_genai_api_error_code_503():
    assert _classify_exit_code(_FakeAPIError(503)) == _EXIT_GEMINI_TRANSIENT


def test_classify_raw_genai_api_error_code_504():
    assert _classify_exit_code(_FakeAPIError(504)) == _EXIT_GEMINI_TRANSIENT


def test_classify_raw_genai_api_error_code_429():
    assert _classify_exit_code(_FakeAPIError(429)) == _EXIT_GEMINI_429


def test_classify_unrelated_exception_returns_generic_failure():
    assert _classify_exit_code(ValueError("unrelated")) == 1


def test_classify_500_is_not_retried():
    """500 INTERNAL is in the 5xx range but isn't on the retry allow-list
    because it usually indicates a real Google-side regression that won't
    clear in 30s. Pin the boundary so adding 500 to the retry set is an
    explicit decision, not a silent expansion."""
    assert _classify_exit_code(_FakeLLMPipelineError(500)) == 1


def test_classify_walks_chained_cause():
    """A 503 wrapped behind a generic re-raise still classifies as transient."""
    try:
        try:
            raise _FakeLLMPipelineError(503)
        except Exception as inner:
            raise RuntimeError("eval runner re-raised") from inner
    except RuntimeError as exc:
        assert _classify_exit_code(exc) == _EXIT_GEMINI_TRANSIENT


def test_extract_http_status_returns_raw_code_for_diagnostics():
    """The friendly eval-gate message prints the actual status so triage
    doesn't have to guess between 502/503/504."""
    assert _extract_http_status(_FakeLLMPipelineError(504)) == 504
    assert _extract_http_status(_FakeAPIError(429)) == 429
    assert _extract_http_status(ValueError("nothing http about this")) is None


def test_append_step_summary_writes_when_env_set(tmp_path, monkeypatch):
    """When ``$GITHUB_STEP_SUMMARY`` is set, the helper appends Markdown
    to that file. The eval-gate handler uses this to surface the failure
    reason on the GitHub run page."""
    summary_path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    _append_step_summary("### first block\nbody")
    _append_step_summary("### second block")
    contents = summary_path.read_text(encoding="utf-8")
    assert "### first block" in contents
    assert "### second block" in contents
    # Each block separated by a blank line so they render as distinct
    # Markdown sections.
    assert "body\n\n### second block" in contents


def test_append_step_summary_noop_when_env_unset(tmp_path, monkeypatch):
    """Outside CI the env var is missing - the helper must do nothing
    (no exception, no file created)."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    # Should simply return None.
    assert _append_step_summary("anything") is None


def test_append_step_summary_swallows_io_errors(monkeypatch):
    """A summary write failure must never mask the real failure that
    prompted the write."""
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", "/nonexistent/dir/summary.md")
    # Should not raise even though the path is unwritable.
    _append_step_summary("anything")


def test_exit_codes_match_workflow_contract():
    """The numbers are an explicit contract with .github/workflows/ci.yml.

    If you change either, change the other (search the workflow for
    ``retry_on_exit_code``).
    """
    assert _EXIT_GEMINI_TRANSIENT == 76
    assert _EXIT_GEMINI_429 == 75
