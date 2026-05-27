"""Tests for the cross-agent LLM helpers in ``agents/_llm_utils.py``.

The two helpers are defensive wrappers around quirks of the
langchain-google-genai message shape. Tests pin the contract for each
quirk so a future Gemini SDK upgrade can't silently regress the agents.
"""

from __future__ import annotations

from agents._llm_utils import coerce_response_text, strip_json_fence

# ---------------------------------------------------------------------------
# coerce_response_text - handles the gemini-2.5 → gemini-3.5 content drift
# ---------------------------------------------------------------------------


def test_coerce_plain_string_returned_unchanged() -> None:
    """gemini-2.5-flash and earlier returned content as a plain string."""
    assert coerce_response_text("hello world") == "hello world"


def test_coerce_list_of_blocks_extracts_text() -> None:
    """gemini-3.5-flash returns a list of content blocks; we want only the text."""
    content = [{"type": "text", "text": '{"intent": "foo"}', "extras": {"signature": "abc"}}]
    assert coerce_response_text(content) == '{"intent": "foo"}'


def test_coerce_multiple_text_blocks_concatenated() -> None:
    content = [
        {"type": "text", "text": "first "},
        {"type": "text", "text": "second"},
    ]
    assert coerce_response_text(content) == "first second"


def test_coerce_skips_non_text_blocks() -> None:
    """Non-'text' typed blocks (e.g. thinking) are skipped, not concatenated."""
    content = [
        {"type": "thinking", "text": "should be ignored"},
        {"type": "text", "text": "real payload"},
    ]
    assert coerce_response_text(content) == "real payload"


def test_coerce_falls_back_to_str_on_unknown_shape() -> None:
    """Anything else falls back to str() - same coarse behaviour the agents had
    before this helper, so JSON-parse downstream surfaces the issue loudly."""
    assert coerce_response_text(42) == "42"
    assert coerce_response_text(None) == "None"


def test_coerce_empty_list_falls_back_to_str() -> None:
    """An empty list has no text blocks → fall back rather than return empty."""
    assert coerce_response_text([]) == "[]"


# ---------------------------------------------------------------------------
# strip_json_fence - existing helper, regression guard
# ---------------------------------------------------------------------------


def test_strip_json_fence_passthrough_when_no_fence() -> None:
    assert strip_json_fence('{"a": 1}') == '{"a": 1}'


def test_strip_json_fence_strips_json_fence() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert strip_json_fence(raw) == '{"a": 1}'


def test_strip_json_fence_strips_bare_fence() -> None:
    raw = '```\n{"a": 1}\n```'
    assert strip_json_fence(raw) == '{"a": 1}'
