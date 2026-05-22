"""Tests for the Phase 12.5 LLM-failure conversion + stub-artifact path.

Covers:
- ``LLMFailureDetail`` JSON shape (stable contract for downstream consumers).
- ``wrap_llm_exception`` classification across Gemini SDK classes + transport errors.
- ``LLMPipelineError`` message format and ``detail`` attribute.
- ``is_retryable_llm_failure`` mirrors the SDK's retry decision.
- ``build_stub_artifact_from_error`` produces a FinalUserStory with the
  structured error encoded in metadata.
- Each of the five agent functions wraps its LLM exception correctly when
  the LangChain invoke raises (monkeypatched).
"""

from __future__ import annotations

import json

import pytest
from google.genai import errors as gemini_errors

from agents._llm_errors import (
    LLMFailureDetail,
    LLMPipelineError,
    PipelineStepError,
    is_retryable_llm_failure,
    wrap_llm_exception,
    wrap_step_exception,
)
from agents.graph import build_stub_artifact_from_error

# ---------------------------------------------------------------------------
# LLMFailureDetail — JSON contract
# ---------------------------------------------------------------------------


def _make_detail(**overrides: object) -> LLMFailureDetail:
    """Build an LLMFailureDetail with sensible defaults for tests."""
    defaults: dict[str, object] = {
        "agent": "requirements_analyst",
        "error_class": "ServerError",
        "http_status": 503,
        "message": "Service unavailable, please retry later",
        "retries_attempted": 5,
        "occurred_at": "2026-05-22T13:45:00+00:00",
    }
    defaults.update(overrides)
    return LLMFailureDetail(**defaults)  # type: ignore[arg-type]


def test_detail_json_round_trips():
    """JSON encoding is symmetric — no field is silently dropped."""
    detail = _make_detail()
    parsed = json.loads(detail.to_json())
    assert parsed == {
        "agent": "requirements_analyst",
        "error_class": "ServerError",
        "http_status": 503,
        "message": "Service unavailable, please retry later",
        "retries_attempted": 5,
        "occurred_at": "2026-05-22T13:45:00+00:00",
    }


def test_detail_json_is_compact():
    """The structural separators have no whitespace — keeps the payload small.

    Checks the separators between FIELDS (``","`` and ``":"``) rather than
    arbitrary text bytes — the human message field can legitimately
    contain ``", "`` ("Service unavailable, please retry later") and that
    is content, not formatting overhead. The compactness contract is
    about Python's ``json.dumps(separators=(",", ":"))`` mode being used.
    """
    detail = _make_detail()
    encoded = detail.to_json()
    # Structural patterns that would appear ONLY in pretty-printed JSON:
    # `", "` between fields (preceded by a closing quote)
    # `": "` between keys and values (preceded by a closing quote)
    assert '", "' not in encoded
    assert '": "' not in encoded


def test_detail_allows_null_http_status():
    """Transport-level failures (TimeoutError etc.) have no HTTP status."""
    detail = _make_detail(http_status=None, error_class="TimeoutError")
    parsed = json.loads(detail.to_json())
    assert parsed["http_status"] is None


# ---------------------------------------------------------------------------
# wrap_llm_exception — Gemini SDK class mapping
# ---------------------------------------------------------------------------


def test_wrap_server_error_extracts_status_and_message():
    """A 503 ServerError yields http_status=503 and a populated message."""
    exc = gemini_errors.ServerError(code=503, response_json={"error": {"message": "overloaded"}})
    wrapped = wrap_llm_exception("requirements_analyst", exc, retries_attempted=5)
    assert isinstance(wrapped, LLMPipelineError)
    assert wrapped.detail.agent == "requirements_analyst"
    assert wrapped.detail.error_class == "ServerError"
    assert wrapped.detail.http_status == 503
    assert wrapped.detail.retries_attempted == 5


def test_wrap_client_error_extracts_status():
    """4xx client errors are surfaced with their code."""
    exc = gemini_errors.ClientError(
        code=429, response_json={"error": {"message": "quota exceeded"}}
    )
    wrapped = wrap_llm_exception("ac_generator", exc, retries_attempted=5)
    assert wrapped.detail.http_status == 429
    assert wrapped.detail.error_class == "ClientError"
    assert wrapped.detail.agent == "ac_generator"


def test_wrap_timeout_error_has_no_http_status():
    """Transport-level errors (TimeoutError) yield http_status=None."""
    exc = TimeoutError("read timed out after 30s")
    wrapped = wrap_llm_exception("reviewer", exc, retries_attempted=5)
    assert wrapped.detail.http_status is None
    assert wrapped.detail.error_class == "TimeoutError"
    assert "timed out" in wrapped.detail.message


def test_wrap_connection_error_has_no_http_status():
    """Network blips arrive as ConnectionError — also no HTTP status."""
    exc = ConnectionError("connection reset by peer")
    wrapped = wrap_llm_exception("story_writer", exc, retries_attempted=5)
    assert wrapped.detail.http_status is None
    assert wrapped.detail.error_class == "ConnectionError"


def test_wrap_unknown_exception_falls_back_gracefully():
    """Unrecognised exceptions still produce a structured error, not a crash."""
    exc = ValueError("something unexpected")
    wrapped = wrap_llm_exception("test_case_writer", exc, retries_attempted=5)
    assert wrapped.detail.http_status is None
    assert wrapped.detail.error_class == "ValueError"
    assert "unexpected" in wrapped.detail.message


def test_wrap_truncates_long_messages():
    """An overlong server message gets capped to prevent metadata bloat."""
    long_msg = "x" * 5_000
    exc = gemini_errors.ServerError(code=500, response_json={"error": {"message": long_msg}})
    wrapped = wrap_llm_exception("requirements_analyst", exc, retries_attempted=5)
    assert len(wrapped.detail.message) <= 501  # cap + ellipsis


def test_pipeline_error_message_includes_attribution():
    """The exception str() carries agent name + status for human debugging."""
    exc = gemini_errors.ServerError(code=503, response_json={"error": {"message": "boom"}})
    wrapped = wrap_llm_exception("requirements_analyst", exc, retries_attempted=5)
    text = str(wrapped)
    assert "requirements_analyst" in text
    assert "ServerError" in text
    assert "503" in text


# ---------------------------------------------------------------------------
# is_retryable_llm_failure — mirrors SDK retry policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_is_retryable_for_5xx(status):
    exc = gemini_errors.ServerError(code=status, response_json={"error": {"message": "x"}})
    assert is_retryable_llm_failure(exc) is True


def test_is_retryable_for_429():
    exc = gemini_errors.ClientError(code=429, response_json={"error": {"message": "x"}})
    assert is_retryable_llm_failure(exc) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_not_retryable_for_4xx_other_than_429(status):
    exc = gemini_errors.ClientError(code=status, response_json={"error": {"message": "x"}})
    assert is_retryable_llm_failure(exc) is False


def test_is_retryable_for_transport_errors():
    assert is_retryable_llm_failure(TimeoutError("x")) is True
    assert is_retryable_llm_failure(ConnectionError("x")) is True


def test_not_retryable_for_value_error():
    """Programmer errors (malformed prompt etc.) are not retry-worthy."""
    assert is_retryable_llm_failure(ValueError("bad input")) is False


# ---------------------------------------------------------------------------
# build_stub_artifact_from_error — graph helper
# ---------------------------------------------------------------------------


def test_stub_artifact_carries_json_error_in_metadata():
    """The stub artifact's metadata['error'] parses back to the original detail."""
    detail = _make_detail()
    error = LLMPipelineError(detail)
    artifact = build_stub_artifact_from_error(error)

    assert "error" in artifact.metadata
    parsed = json.loads(artifact.metadata["error"])
    assert parsed["agent"] == "requirements_analyst"
    assert parsed["http_status"] == 503


def test_stub_artifact_has_human_readable_review_summary():
    """metadata['review_summary'] surfaces the failure inline in rendered markdown."""
    detail = _make_detail()
    error = LLMPipelineError(detail)
    artifact = build_stub_artifact_from_error(error)

    summary = artifact.metadata.get("review_summary", "")
    assert "ERROR" in summary
    assert "requirements_analyst" in summary
    assert "ServerError" in summary
    assert "503" in summary


def test_stub_artifact_sections_are_placeholders():
    """description / objective explicitly mark the abort — no empty strings."""
    detail = _make_detail()
    error = LLMPipelineError(detail)
    artifact = build_stub_artifact_from_error(error)
    assert "aborted" in artifact.description.lower()
    assert "metadata.error" in artifact.description
    assert artifact.acceptance_criteria == []
    assert artifact.test_cases == []


def test_stub_artifact_renders_via_as_markdown():
    """The stub still renders via the normal as_markdown path."""
    detail = _make_detail()
    error = LLMPipelineError(detail)
    artifact = build_stub_artifact_from_error(error)
    md = artifact.as_markdown()
    assert "## Description" in md
    assert "## Objective" in md
    # The review_summary key surfaces in the metadata section.
    assert "review_summary" in md
    assert "ERROR" in md


# ---------------------------------------------------------------------------
# Agent integration — monkeypatch LangChain invoke to raise, assert wrap
# ---------------------------------------------------------------------------


class _FakeLLMRaising:
    """Stand-in for ChatGoogleGenerativeAI whose invoke raises a ServerError."""

    def __init__(self, *args, **kwargs):
        pass

    def invoke(self, messages, config=None):
        raise gemini_errors.ServerError(
            code=503, response_json={"error": {"message": "overloaded"}}
        )


def test_analyze_requirement_wraps_llm_exception(monkeypatch):
    """The Analyst converts a raw Gemini exception into LLMPipelineError."""
    from agents import requirements_analyst as ra

    monkeypatch.setattr(ra, "ChatGoogleGenerativeAI", _FakeLLMRaising)
    with pytest.raises(LLMPipelineError) as excinfo:
        ra.analyze_requirement("any text")
    assert excinfo.value.detail.agent == "requirements_analyst"
    assert excinfo.value.detail.http_status == 503


def test_write_user_story_wraps_llm_exception(monkeypatch):
    from agents import user_story_writer as usw
    from agents.requirements_analyst import AnalystOutput

    monkeypatch.setattr(usw, "ChatGoogleGenerativeAI", _FakeLLMRaising)
    analyst_stub = AnalystOutput(intent="stub", actors=["user"], ambiguities=[], implied_stories=[])
    with pytest.raises(LLMPipelineError) as excinfo:
        usw.write_user_story(analyst_stub, {})
    assert excinfo.value.detail.agent == "user_story_writer"


def test_generate_acceptance_criteria_wraps_llm_exception(monkeypatch):
    from agents import ac_generator as acg
    from agents.requirements_analyst import AnalystOutput
    from agents.user_story_writer import UserStory

    monkeypatch.setattr(acg, "ChatGoogleGenerativeAI", _FakeLLMRaising)
    analyst_stub = AnalystOutput(intent="stub", actors=["user"], ambiguities=[], implied_stories=[])
    story_stub = UserStory(
        description="As a user, I want X, so that Y.",
        objective="Y",
        assumptions=[],
    )
    with pytest.raises(LLMPipelineError) as excinfo:
        acg.generate_acceptance_criteria(story_stub, analyst_stub, {})
    assert excinfo.value.detail.agent == "ac_generator"


def test_write_test_cases_wraps_llm_exception(monkeypatch):
    from agents import test_case_writer as tcw
    from agents.final_artifact import AcceptanceCriterion
    from agents.user_story_writer import UserStory

    monkeypatch.setattr(tcw, "ChatGoogleGenerativeAI", _FakeLLMRaising)
    story_stub = UserStory(
        description="As a user, I want X, so that Y.",
        objective="Y",
        assumptions=[],
    )
    acs_stub = [AcceptanceCriterion(given="given", when="when", then="then")]
    with pytest.raises(LLMPipelineError) as excinfo:
        tcw.write_test_cases(story_stub, acs_stub)
    assert excinfo.value.detail.agent == "test_case_writer"


def test_review_artifact_wraps_llm_exception(monkeypatch):
    from agents import reviewer as rv
    from agents.final_artifact import FinalUserStory

    monkeypatch.setattr(rv, "ChatGoogleGenerativeAI", _FakeLLMRaising)
    artifact = FinalUserStory(
        description="As a user, I want X, so that Y.",
        objective="Y",
    )
    with pytest.raises(LLMPipelineError) as excinfo:
        rv.review_artifact("any requirement", artifact)
    assert excinfo.value.detail.agent == "reviewer"


# ---------------------------------------------------------------------------
# Phase 13.4 — non-LLM step failures wrapped at node boundary
# ---------------------------------------------------------------------------


def test_wrap_step_exception_produces_pipeline_step_error_with_no_http_status():
    """Non-LLM exceptions carry http_status=None and retries_attempted=0."""
    exc = ValueError("malformed JSON from upstream")
    wrapped = wrap_step_exception("ac_generator", exc)
    assert isinstance(wrapped, PipelineStepError)
    # NOT an LLMPipelineError — the narrower subclass is for actual API calls.
    assert not isinstance(wrapped, LLMPipelineError)
    assert wrapped.detail.agent == "ac_generator"
    assert wrapped.detail.error_class == "ValueError"
    assert wrapped.detail.http_status is None
    assert wrapped.detail.retries_attempted == 0
    assert "malformed JSON" in wrapped.detail.message


def test_pipeline_step_error_message_omits_llm_specific_suffix_when_non_llm():
    """Non-LLM detail prints a cleaner message — no '(status=None, retries=0)' noise."""
    wrapped = wrap_step_exception("composer", RuntimeError("boom"))
    msg = str(wrapped)
    assert "Pipeline step failed" in msg
    assert "composer" in msg
    assert "RuntimeError" in msg
    # The LLM-specific fields would be lies for a non-LLM failure; check
    # they're not formatted in.
    assert "status=" not in msg
    assert "retries=" not in msg


def test_llm_pipeline_error_is_subclass_of_pipeline_step_error():
    """One catch surface — callers can `except PipelineStepError` for both."""
    detail = _make_detail()
    err = LLMPipelineError(detail)
    assert isinstance(err, PipelineStepError)


def test_build_stub_artifact_from_step_error_uses_step_summary_form():
    """Stub artifact metadata distinguishes step failures from LLM failures."""
    exc = ValueError("schema validation failed")
    wrapped = wrap_step_exception("test_case_writer", exc)
    artifact = build_stub_artifact_from_error(wrapped)
    summary = artifact.metadata["review_summary"]
    assert "Step failure" in summary
    assert "test_case_writer" in summary
    assert "ValueError" in summary
    # Error JSON still parseable — same downstream contract as LLM path.
    detail_json = json.loads(artifact.metadata["error"])
    assert detail_json["agent"] == "test_case_writer"
    assert detail_json["http_status"] is None
    assert detail_json["retries_attempted"] == 0


def test_node_wrapper_converts_unexpected_exception_to_pipeline_step_error():
    """A node that raises ValueError is caught and surfaced as PipelineStepError."""
    from agents.graph import _wrap_node_exceptions

    def broken_node(state):
        raise ValueError("upstream produced garbage")

    wrapped = _wrap_node_exceptions("ac_generator", broken_node)
    with pytest.raises(PipelineStepError) as excinfo:
        wrapped({})
    assert excinfo.value.detail.agent == "ac_generator"
    assert excinfo.value.detail.error_class == "ValueError"
    assert "garbage" in excinfo.value.detail.message
    # And it's NOT an LLMPipelineError — we did not lie about the source.
    assert not isinstance(excinfo.value, LLMPipelineError)


def test_node_wrapper_passes_llm_pipeline_error_through_unchanged():
    """LLMPipelineError carries its own structured detail; don't double-wrap."""
    from agents.graph import _wrap_node_exceptions

    original = LLMPipelineError(_make_detail(agent="reviewer"))

    def llm_failing_node(state):
        raise original

    wrapped = _wrap_node_exceptions("ac_generator", llm_failing_node)
    with pytest.raises(LLMPipelineError) as excinfo:
        wrapped({})
    # Identity-preserved: same exception object, same agent attribution
    # (reviewer, not the wrapping node's ac_generator).
    assert excinfo.value is original
    assert excinfo.value.detail.agent == "reviewer"


def test_node_wrapper_lets_graph_bubble_up_propagate():
    """LangGraph's GraphInterrupt etc. are control-flow, not failures."""
    from langgraph.errors import GraphInterrupt
    from langgraph.types import Interrupt

    from agents.graph import _wrap_node_exceptions

    payload = Interrupt(value={"ask": "test"}, id="x")

    def interrupting_node(state):
        raise GraphInterrupt((payload,))

    wrapped = _wrap_node_exceptions("po_checkpoint", interrupting_node)
    with pytest.raises(GraphInterrupt):
        wrapped({})


def test_node_wrapper_lets_keyboard_interrupt_propagate():
    """User Ctrl-C must not be swallowed into a stub artifact."""
    from agents.graph import _wrap_node_exceptions

    def interruptible(state):
        raise KeyboardInterrupt

    wrapped = _wrap_node_exceptions("analyst", interruptible)
    with pytest.raises(KeyboardInterrupt):
        wrapped({})
