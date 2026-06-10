"""Tests for `agents.config` - the shared agent configuration module.

These tests pin the contract of `prompt_hash` because LangSmith trace
metadata depends on it being deterministic, sensitive to content, and a
stable length. A regression here would silently break eval-baseline
attribution downstream.
"""

from __future__ import annotations

from agents.config import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    prompt_hash,
)


def test_defaults_are_present_and_typed():
    """Shared defaults are exposed and have the expected shapes."""
    assert isinstance(DEFAULT_MODEL, str) and DEFAULT_MODEL.startswith("gemini-")
    assert isinstance(DEFAULT_TIMEOUT_SECONDS, float) and DEFAULT_TIMEOUT_SECONDS > 0
    assert isinstance(DEFAULT_MAX_RETRIES, int) and DEFAULT_MAX_RETRIES >= 0


def test_default_max_retries_in_resilience_window():
    """Patience budget must ride out brief load events without wedging.

    Window retuned in issue #163 (pipeline speedup). Prior bound was
    [4, 10] tracking ADR-0003's Claude-era budget; with the workflow-level
    ``nick-fields/retry@v4`` (2 outer attempts) added since, SDK
    ``max_retries=2`` gives a layered worst case of 2×2=4 logical
    attempts - still enough to ride a single transient 503, but tail
    latency is bounded. Lower bound (>=1) catches an accidental
    disable; upper bound (<=4) catches a re-introduction of the prior
    high-tail-latency regime now that the outer retry exists.
    """
    assert 1 <= DEFAULT_MAX_RETRIES <= 4, (
        f"DEFAULT_MAX_RETRIES={DEFAULT_MAX_RETRIES} is outside the "
        "resilience window. See ADR-0003 and issue #163 before tuning."
    )


def test_prompt_hash_is_deterministic():
    """Same input bytes must always produce the same hash."""
    assert prompt_hash("hello") == prompt_hash("hello")
    assert prompt_hash("a", "b") == prompt_hash("a", "b")


def test_prompt_hash_returns_short_hex():
    """Output is 12-char hex - short enough for trace UIs, long enough to be unique."""
    result = prompt_hash("anything")
    assert len(result) == 12
    assert all(c in "0123456789abcdef" for c in result), f"Not lowercase hex: {result!r}"


def test_prompt_hash_changes_when_any_prompt_changes():
    """Editing ANY prompt segment must produce a different hash.

    Without this guarantee, a prompt update could silently keep the same
    hash and break eval-baseline attribution.
    """
    base = prompt_hash("system prompt", "user template")
    assert base != prompt_hash("system prompt CHANGED", "user template")
    assert base != prompt_hash("system prompt", "user template CHANGED")
    assert base != prompt_hash("system prompt", "user template", "extra")


def test_prompt_hash_argument_order_matters():
    """Both prompts contribute to model behavior; reordering must change the hash.

    Otherwise swapping system and user templates would silently keep the
    same identifier - a real correctness risk if downstream tools assume
    the order is part of the version.
    """
    assert prompt_hash("a", "b") != prompt_hash("b", "a")


def test_prompt_hash_distinguishes_split_arguments_from_concatenated():
    """('ab', 'c') must produce a different hash from ('a', 'bc').

    This pins the delimiter behavior. Without it, the hash collapses
    multi-argument inputs into "the bytes are the same" and loses
    structural information.
    """
    assert prompt_hash("ab", "c") != prompt_hash("a", "bc")
