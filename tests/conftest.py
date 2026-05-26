"""Shared pytest fixtures for the RTIA test suite.

Currently scoped to one concern: setting dummy provider API keys before
any agent module imports / instantiates an LLM client.

Why: ``langchain_google_genai.ChatGoogleGenerativeAI`` validates the API
key at construction time (raises ``ValidationError`` if ``GOOGLE_API_KEY``
is missing). The Anthropic client defers validation to ``invoke`` time, so
it doesn't need this. Tests mock ``.invoke`` but still construct real
instances of the Gemini class, so a placeholder key has to exist.

Using ``autouse=True`` so every test gets the fixture without needing to
opt in — this is environment setup, not a test-shaped dependency.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _dummy_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure provider API keys exist for client construction in tests.

    Only sets the var if absent — a developer running tests with a real
    key in their environment keeps it. ``monkeypatch`` restores the env
    after each test so the fixture is non-leaky.
    """
    if not os.environ.get("GOOGLE_API_KEY"):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-placeholder-google-key")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder-anthropic-key")


@pytest.fixture(autouse=True)
def _disable_llm_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the LLM response cache for every test by default.

    Mocked tests patch ``llm.invoke`` per-call; a stale cache entry from a
    previous test would shadow the new mock and return cached text instead
    of letting the mock run. Per-test cache isolation via temp dirs would
    also work but is heavier — disabling is the simpler invariant for the
    mocked-LLM contract tests this suite is built around.

    Tests that specifically exercise the cache (``tests/test_llm_cache.py``)
    re-enable it explicitly via ``monkeypatch.setenv`` inside the test.
    """
    monkeypatch.setenv("RTIA_LLM_CACHE", "disabled")
