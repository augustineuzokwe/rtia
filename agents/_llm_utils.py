"""Cross-agent LLM helpers.

Lives here rather than in any single agent module because all four agents
post-Gemini-cutover share the same defensive needs:

- Stripping the optional ``` ```json `` ``` fences Gemini occasionally
  wraps structured-output in despite the prompt's "no fences" instruction.
- Coalescing the LangChain ``message.content`` field - older Gemini
  models returned a plain string here; gemini-3.5-flash and later return
  a list of content blocks (``[{'type': 'text', 'text': '...'}, ...]``).
  ``coerce_response_text`` handles both.

Underscore-prefixed module name marks it as project-private - nothing
outside ``agents/`` should import from it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import diskcache

from agents.config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL_SECONDS,
    LLM_CACHE_DIR_ENV_VAR,
    LLM_CACHE_ENABLED_ENV_VAR,
    LLM_CACHE_TTL_ENV_VAR,
)


def coerce_response_text(content: Any) -> str:
    """Return the plain-text payload of a LangChain message ``content`` field.

    Gemini-2.5-flash and older return plain strings; gemini-3.5-flash
    returns a list of content blocks like
    ``[{'type': 'text', 'text': '…', 'extras': {...}}]``. We extract and
    concatenate every ``text`` field from ``type == 'text'`` blocks.

    Non-text blocks (e.g. ``thinking`` if the model ever emits them
    alongside text) are skipped - the agents parse structured JSON from
    the text-only payload.

    Anything that isn't a string or a list of dicts falls back to
    ``str()`` - that's the same coarse behaviour the agents had before
    this helper, and surfaces an obvious malformed value loudly enough
    that the JSON parser downstream will complain.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    return str(content)


_CACHE_SINGLETON: diskcache.Cache | None = None


def _cache_enabled() -> bool:
    return os.environ.get(LLM_CACHE_ENABLED_ENV_VAR, "enabled").strip().lower() == "enabled"


def _cache_ttl_seconds() -> int:
    raw = os.environ.get(LLM_CACHE_TTL_ENV_VAR)
    if raw is None:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        ttl = int(raw)
        return ttl if ttl > 0 else DEFAULT_CACHE_TTL_SECONDS
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS


def _cache_dir() -> Path:
    raw = os.environ.get(LLM_CACHE_DIR_ENV_VAR)
    return Path(raw).expanduser() if raw else DEFAULT_CACHE_DIR


def _get_cache() -> diskcache.Cache:
    """Process-singleton diskcache instance backed by ~/.rtia/cache by default."""
    global _CACHE_SINGLETON
    if _CACHE_SINGLETON is None:
        dir_path = _cache_dir()
        dir_path.mkdir(parents=True, exist_ok=True)
        _CACHE_SINGLETON = diskcache.Cache(str(dir_path))
    return _CACHE_SINGLETON


def _reset_cache_singleton_for_tests() -> None:
    """Drop the cached singleton so a test can reconfigure ``RTIA_LLM_CACHE_DIR``.

    Test-only helper. Production code never needs this - env vars are read
    once per process and the cache directory is stable.
    """
    global _CACHE_SINGLETON
    if _CACHE_SINGLETON is not None:
        _CACHE_SINGLETON.close()
        _CACHE_SINGLETON = None


def _message_to_canonical(message: Any) -> dict[str, str]:
    """Reduce a LangChain message to a stable (type, text) pair for hashing.

    Cache key stability depends on bit-for-bit identical input across runs.
    LangChain message objects carry mutable metadata (id, name, additional_kwargs)
    that can vary between runs without changing the prompt; only the message
    role and the textual content matter for "did the model see the same thing?"
    """
    return {
        "type": type(message).__name__,
        "content": coerce_response_text(getattr(message, "content", "")),
    }


def _make_cache_key(model_id: str, prompt_hash: str, messages: list[Any]) -> str:
    """Build a stable sha256 cache key from (model, prompt version, messages).

    Includes ``prompt_hash`` so a prompt edit auto-invalidates the cache
    entry (the key changes, the lookup misses, the next call hits the
    model live). This is the load-bearing defence against the "false-green
    CI" trap documented in [Issue #230](https://github.com/augustineuzokwe/rtia/issues/230).
    """
    payload = {
        "model_id": model_id,
        "prompt_hash": prompt_hash,
        "messages": [_message_to_canonical(m) for m in messages],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"rtia:llm:{digest}"


class CachedResponse:
    """Shim returned on cache hit; exposes the attributes agents read.

    Agents pull ``.content`` (the textual payload) and the logging helpers
    pull ``.usage_metadata`` for token counting. On a cache hit there are
    no real token costs to report, so ``usage_metadata`` is ``None`` - the
    extractor in ``agents/_logging.py:_extract_token_usage`` treats missing
    metadata as ``None`` for each field rather than zero, which keeps the
    telemetry honest (no live call → no count, not zero count).

    ``response_metadata`` carries ``cache_hit=True`` so downstream code can
    detect cached responses without inspecting the type.
    """

    def __init__(self, content: str) -> None:
        self.content: str = content
        self.usage_metadata: dict[str, int] | None = None
        self.response_metadata: dict[str, Any] = {"cache_hit": True}


def cached_invoke(
    llm: Any,
    messages: list[Any],
    *,
    model_id: str,
    prompt_hash: str,
    config: dict[str, Any] | None = None,
) -> Any:
    """Invoke ``llm`` with an optional disk-backed response cache.

    Cache key = sha256(model_id + prompt_hash + canonicalised messages).
    TTL is read from ``RTIA_LLM_CACHE_TTL`` (default 24 h). The cache is
    bypassed when ``RTIA_LLM_CACHE=disabled`` - set by the CI regression
    job to keep the eval gate honest.

    Returns a LangChain response on cache miss, or a :class:`CachedResponse`
    shim on hit. Both expose ``.content`` (str) and ``.usage_metadata``
    (``None`` on cache hit). Callers should not rely on the return type
    beyond those two attributes.

    Errors from ``llm.invoke`` are propagated unchanged - the caller's
    existing ``wrap_llm_exception`` layer remains responsible for envelope
    semantics. The cache is never populated with an error.
    """
    if not _cache_enabled():
        return llm.invoke(messages, config=config)

    key = _make_cache_key(model_id, prompt_hash, messages)
    cache = _get_cache()
    cached = cache.get(key)
    if isinstance(cached, str):
        return CachedResponse(cached)

    response = llm.invoke(messages, config=config)
    text = coerce_response_text(response.content)
    cache.set(key, text, expire=_cache_ttl_seconds())
    return response


def strip_json_fence(raw: str) -> str:
    """Strip an optional ``` ```json … ``` `` fence from an LLM response.

    Gemini occasionally wraps JSON output in a markdown code fence despite
    being told not to. Trim defensively rather than fight the prompt - the
    inner JSON is what we want and a fence at the boundary is harmless to
    remove. Anthropic models tested in this project don't add fences, so
    the helper is a no-op for them. Idempotent: calling on un-fenced input
    returns the input stripped of surrounding whitespace.
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    # Drop the opening fence line (``` or ```json), and the closing fence
    # if present. Anything between is the JSON we want.
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
