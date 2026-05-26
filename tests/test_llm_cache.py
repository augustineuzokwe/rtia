"""Tests for ``agents._llm_utils.cached_invoke``.

The cache exists to avoid two bugs:

1. Repeated live LLM calls for inputs that haven't changed (cost + speed).
2. The "false-green CI" trap — a stale cache that silently passes the
   eval gate while the model has actually drifted.

These tests assert the design that prevents (2):

- Cache hit returns the SAME stored value for the same key.
- Cache miss makes a live call AND persists the response.
- A prompt-hash change INVALIDATES the cache (different key → miss).
- A model-id change INVALIDATES the cache (different key → miss).
- A messages change INVALIDATES the cache (different key → miss).
- TTL expiry INVALIDATES the cache.
- ``RTIA_LLM_CACHE=disabled`` always bypasses, even with a warm key.

See [Issue #230](https://github.com/augustineuzokwe/rtia/issues/230) for the failure
mode that motivates each assertion.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents import _llm_utils
from agents._llm_utils import (
    CachedResponse,
    _make_cache_key,
    _reset_cache_singleton_for_tests,
    cached_invoke,
)


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate each test to its own cache dir + force-enable the cache."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("RTIA_LLM_CACHE", "enabled")
    monkeypatch.setenv("RTIA_LLM_CACHE_DIR", str(cache_dir))
    _reset_cache_singleton_for_tests()
    yield cache_dir
    _reset_cache_singleton_for_tests()


class _FakeMessage:
    """LangChain-shaped message stand-in for cache-key tests."""

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeResponse:
    """LangChain-shaped response stand-in."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


def _make_llm(response_text: str) -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = _FakeResponse(response_text)
    return llm


def test_cache_miss_then_hit_returns_same_content(tmp_cache_dir: Path) -> None:
    llm = _make_llm("first-response")
    messages = [_FakeMessage("hello")]

    first = cached_invoke(llm, messages, model_id="google:gemini-3.5-flash", prompt_hash="abc")
    second = cached_invoke(llm, messages, model_id="google:gemini-3.5-flash", prompt_hash="abc")

    assert llm.invoke.call_count == 1, "Second call should hit cache, not invoke live"
    assert first.content == "first-response"
    assert second.content == "first-response"
    assert isinstance(second, CachedResponse)
    assert second.usage_metadata is None, "Cache hit must NOT fabricate usage counts"
    assert second.response_metadata == {"cache_hit": True}


def test_cache_hit_does_not_record_usage(tmp_cache_dir: Path) -> None:
    """The honesty invariant: cache hits report None tokens, not zero."""
    llm = _make_llm("x")
    messages = [_FakeMessage("payload")]
    cached_invoke(llm, messages, model_id="m", prompt_hash="h")  # warm
    hit = cached_invoke(llm, messages, model_id="m", prompt_hash="h")
    assert hit.usage_metadata is None


def test_prompt_hash_change_invalidates_cache(tmp_cache_dir: Path) -> None:
    """The load-bearing defence against the false-green trap.

    A prompt edit changes ``prompt_hash``; the cache key changes; the
    lookup misses; the next call hits the model live and we measure the
    new prompt's behaviour.
    """
    llm = _make_llm("response-v1")
    messages = [_FakeMessage("input")]
    cached_invoke(llm, messages, model_id="m", prompt_hash="old-hash")
    llm.invoke.return_value = _FakeResponse("response-v2")

    second = cached_invoke(llm, messages, model_id="m", prompt_hash="new-hash")

    assert llm.invoke.call_count == 2
    assert second.content == "response-v2"


def test_model_id_change_invalidates_cache(tmp_cache_dir: Path) -> None:
    """A provider/model swap must not return cached results from the prior model."""
    llm = _make_llm("gemini-output")
    messages = [_FakeMessage("input")]
    cached_invoke(llm, messages, model_id="google:gemini-3.5-flash", prompt_hash="h")
    llm.invoke.return_value = _FakeResponse("llama-output")

    second = cached_invoke(llm, messages, model_id="ollama:llama3.1:8b", prompt_hash="h")

    assert llm.invoke.call_count == 2
    assert second.content == "llama-output"


def test_messages_change_invalidates_cache(tmp_cache_dir: Path) -> None:
    """Different requirement input → different cache key → live call."""
    llm = _make_llm("for-input-A")
    cached_invoke(llm, [_FakeMessage("input-A")], model_id="m", prompt_hash="h")
    llm.invoke.return_value = _FakeResponse("for-input-B")

    second = cached_invoke(llm, [_FakeMessage("input-B")], model_id="m", prompt_hash="h")

    assert llm.invoke.call_count == 2
    assert second.content == "for-input-B"


def test_disable_env_always_bypasses_cache(
    tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``RTIA_LLM_CACHE=disabled`` must miss even when an entry exists.

    This is the CI regression-job invariant — we cannot trust the cache
    on the gate, period.
    """
    llm = _make_llm("first")
    messages = [_FakeMessage("x")]
    cached_invoke(llm, messages, model_id="m", prompt_hash="h")
    assert llm.invoke.call_count == 1

    monkeypatch.setenv("RTIA_LLM_CACHE", "disabled")
    llm.invoke.return_value = _FakeResponse("fresh")

    response = cached_invoke(llm, messages, model_id="m", prompt_hash="h")
    assert llm.invoke.call_count == 2
    assert response.content == "fresh"


def test_ttl_expiry_invalidates_cache(tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TTL of 1s + a brief sleep proves stale entries don't replay forever."""
    monkeypatch.setenv("RTIA_LLM_CACHE_TTL", "1")
    llm = _make_llm("first")
    messages = [_FakeMessage("payload")]

    cached_invoke(llm, messages, model_id="m", prompt_hash="h")
    assert llm.invoke.call_count == 1

    time.sleep(1.2)
    llm.invoke.return_value = _FakeResponse("post-expiry")

    after = cached_invoke(llm, messages, model_id="m", prompt_hash="h")
    assert llm.invoke.call_count == 2
    assert after.content == "post-expiry"


def test_cache_key_is_stable_across_calls(tmp_cache_dir: Path) -> None:
    messages = [_FakeMessage("hello"), _FakeMessage("world")]
    k1 = _make_cache_key("m", "h", messages)
    k2 = _make_cache_key("m", "h", messages)
    assert k1 == k2
    assert k1.startswith("rtia:llm:")


def test_cache_key_differs_for_different_inputs(tmp_cache_dir: Path) -> None:
    base = [_FakeMessage("hello")]
    other_model = _make_cache_key("m1", "h", base)
    other_hash = _make_cache_key("m", "h2", base)
    other_msg = _make_cache_key("m", "h", [_FakeMessage("hi")])
    assert len({other_model, other_hash, other_msg, _make_cache_key("m", "h", base)}) == 4


def test_default_disabled_means_no_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest sets RTIA_LLM_CACHE=disabled; verify no entry persists."""
    cache_dir = tmp_path / "cache_default"
    monkeypatch.setenv("RTIA_LLM_CACHE_DIR", str(cache_dir))
    _reset_cache_singleton_for_tests()

    llm = _make_llm("x")
    messages = [_FakeMessage("payload")]
    cached_invoke(llm, messages, model_id="m", prompt_hash="h")
    cached_invoke(llm, messages, model_id="m", prompt_hash="h")

    assert llm.invoke.call_count == 2, (
        "With RTIA_LLM_CACHE=disabled (autouse fixture default), every call must miss"
    )
    _reset_cache_singleton_for_tests()


def test_cache_directory_created_on_first_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "nonexistent" / "deeply" / "nested"
    monkeypatch.setenv("RTIA_LLM_CACHE", "enabled")
    monkeypatch.setenv("RTIA_LLM_CACHE_DIR", str(cache_dir))
    _reset_cache_singleton_for_tests()

    assert not cache_dir.exists()

    llm = _make_llm("x")
    cached_invoke(llm, [_FakeMessage("y")], model_id="m", prompt_hash="h")

    assert cache_dir.exists()
    _reset_cache_singleton_for_tests()


def test_cache_uses_default_dir_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``RTIA_LLM_CACHE_DIR`` unset → falls back to the configured default.

    We don't actually want this test to write to ``~/.rtia/cache``, so we
    monkeypatch the default constant for the duration of the assertion.
    """
    custom_default = tmp_path / "default-cache"
    monkeypatch.setattr(_llm_utils, "DEFAULT_CACHE_DIR", custom_default)
    monkeypatch.delenv("RTIA_LLM_CACHE_DIR", raising=False)
    monkeypatch.setenv("RTIA_LLM_CACHE", "enabled")
    _reset_cache_singleton_for_tests()

    llm = _make_llm("x")
    cached_invoke(llm, [_FakeMessage("p")], model_id="m", prompt_hash="h")

    assert custom_default.exists()
    _reset_cache_singleton_for_tests()


def test_invalid_ttl_env_falls_back_to_default(
    tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbled ``RTIA_LLM_CACHE_TTL`` must not crash production paths."""
    monkeypatch.setenv("RTIA_LLM_CACHE_TTL", "not-an-integer")
    # Should still complete without raising.
    llm = _make_llm("x")
    cached_invoke(llm, [_FakeMessage("y")], model_id="m", prompt_hash="h")
    cached_invoke(llm, [_FakeMessage("y")], model_id="m", prompt_hash="h")
    # Second call should be a hit (default TTL is 24 h).
    assert llm.invoke.call_count == 1
