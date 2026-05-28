"""Tests for agents/_logging.py.

The interesting cases here are NOT "does Python's logging module work" -
that's a given. They are:

* The JSON formatter emits one parseable object per line with the
  expected schema, including caller-supplied extras.
* The context manager records duration_ms and (on success) token counts
  pulled from a LangChain-shaped response.
* The context manager records error_class/error_message and re-raises
  unchanged on exception.
* configure_logging() is idempotent and doesn't multiply handlers.
* Library import is silent - no handlers are emitting unless
  configure_logging() has been called.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from agents._logging import (
    LOGGER_NAMESPACE,
    JsonFormatter,
    configure_logging,
    get_logger,
    log_agent_invocation,
)


def _capture_handler() -> tuple[logging.Logger, io.StringIO]:
    """Attach a fresh handler to the rtia namespace and return the buffer.

    Cleans up any RTIA-installed handlers first so a previous test's
    configure_logging() doesn't double-emit into this one.
    """
    logger = logging.getLogger(LOGGER_NAMESPACE)
    for handler in list(logger.handlers):
        if getattr(handler, "_rtia", False):
            logger.removeHandler(handler)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    handler._rtia = True  # so configure_logging cleanup also removes it
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, buf


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_formatter_emits_required_fields():
    logger, buf = _capture_handler()
    logger.info("hello", extra={"event": "boot", "agent": "demo"})
    [record] = _lines(buf)
    assert record["level"] == "INFO"
    assert record["logger"] == LOGGER_NAMESPACE
    assert record["event"] == "boot"
    assert record["agent"] == "demo"
    assert record["message"] == "hello"
    assert isinstance(record["ts"], int)


def test_formatter_does_not_leak_reserved_logrecord_attrs():
    logger, buf = _capture_handler()
    logger.info("x")
    [record] = _lines(buf)
    # These would be noise on every line if leaked.
    for noisy in ("args", "msg", "msecs", "pathname", "module"):
        assert noisy not in record


def test_formatter_handles_non_jsonable_extras():
    """Pydantic models, datetimes, etc. should not crash the formatter."""
    logger, buf = _capture_handler()

    class Weird:
        def __str__(self) -> str:
            return "weird-thing"

    logger.info("x", extra={"event": "test", "weird": Weird()})
    [record] = _lines(buf)
    assert record["weird"] == "weird-thing"


class _FakeResponse:
    """Mimics the shape of langchain_google_genai's AIMessage usage_metadata."""

    def __init__(self, usage: dict | None):
        self.usage_metadata = usage


def test_log_agent_invocation_success_captures_tokens_and_duration():
    _, buf = _capture_handler()
    response = _FakeResponse({"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    with log_agent_invocation("requirements_analyst", prompt_hash="abc123") as rec:
        rec.record_response(response)
    end = [r for r in _lines(buf) if r["event"] == "agent_invocation_end"][-1]
    assert end["status"] == "ok"
    assert end["agent"] == "requirements_analyst"
    assert end["prompt_hash"] == "abc123"
    assert end["input_tokens"] == 100
    assert end["output_tokens"] == 50
    assert end["total_tokens"] == 150
    assert isinstance(end["duration_ms"], int)
    assert end["duration_ms"] >= 0


def test_log_agent_invocation_missing_token_metadata_is_none_not_zero():
    """Absent counts must report None - zero would be a lie."""
    _, buf = _capture_handler()
    response = _FakeResponse(usage=None)
    with log_agent_invocation("analyst") as rec:
        rec.record_response(response)
    end = [r for r in _lines(buf) if r["event"] == "agent_invocation_end"][-1]
    assert end["input_tokens"] is None
    assert end["output_tokens"] is None
    assert end["total_tokens"] is None


def test_log_agent_invocation_fallback_to_response_metadata():
    """Older provider shape: response_metadata['token_usage']{prompt,completion,total}."""
    _, buf = _capture_handler()

    class OldResponse:
        usage_metadata = None
        response_metadata = {
            "token_usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}
        }

    with log_agent_invocation("analyst") as rec:
        rec.record_response(OldResponse())
    end = [r for r in _lines(buf) if r["event"] == "agent_invocation_end"][-1]
    assert end["input_tokens"] == 12
    assert end["output_tokens"] == 8
    assert end["total_tokens"] == 20


def test_log_agent_invocation_records_error_and_reraises():
    _, buf = _capture_handler()

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom), log_agent_invocation("analyst") as _rec:
        raise Boom("rate-limited")
    end = [r for r in _lines(buf) if r["event"] == "agent_invocation_end"][-1]
    assert end["status"] == "error"
    assert end["error_class"] == "Boom"
    assert end["error_message"] == "rate-limited"
    assert end["level"] == "ERROR"


def test_configure_logging_is_idempotent():
    """Repeated calls must not multiply handlers."""
    configure_logging(force=True)
    handlers_after_first = len(logging.getLogger(LOGGER_NAMESPACE).handlers)
    configure_logging()
    configure_logging()
    handlers_after_third = len(logging.getLogger(LOGGER_NAMESPACE).handlers)
    assert handlers_after_first == handlers_after_third


def test_get_logger_normalises_namespace():
    assert get_logger("agents.foo").name == "rtia.agents.foo"
    assert get_logger("rtia.agents.foo").name == "rtia.agents.foo"
    assert get_logger("rtia").name == "rtia"
