"""Tests for the optional Ollama judge (``RTIA_OLLAMA_JUDGE``).

Two switches are involved and they are deliberately independent:

- ``RTIA_LLM_PROVIDER`` — generator agents (5 production + eval analyst).
- ``RTIA_OLLAMA_JUDGE`` — deepeval judge.

These assertions defend the design that:

1. The judge stays on Gemini by default, even when the generator is
   swapped — that's the §7.3 apples-to-apples invariant.
2. Opting into ``RTIA_OLLAMA_JUDGE=1`` returns a ``ChatOllama`` instance
   from ``GeminiJudge.load_model`` so the eval suite can run at strictly
   $0 API spend.
3. The truthy-set is conservative (``1 / true / yes / on``) so a typo
   doesn't silently swap the judge.
"""

from __future__ import annotations

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.config import use_ollama_judge


class TestUseOllamaJudge:
    def test_unset_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTIA_OLLAMA_JUDGE", raising=False)
        assert use_ollama_judge() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("RTIA_OLLAMA_JUDGE", value)
        assert use_ollama_judge() is True

    @pytest.mark.parametrize(
        "value",
        ["0", "false", "no", "off", "", "ollama", "enabled", "Y", "T", "anything-else"],
    )
    def test_falsy_or_unknown_values_stay_off(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        # Conservative interpretation: only an explicit truthy value flips
        # the judge. A typo or ambiguous string keeps the safer default.
        monkeypatch.setenv("RTIA_OLLAMA_JUDGE", value)
        assert use_ollama_judge() is False


class TestJudgeLoadModel:
    def test_default_returns_gemini(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTIA_OLLAMA_JUDGE", raising=False)
        from evals.judge import GeminiJudge

        judge = GeminiJudge()
        model = judge.load_model()
        assert isinstance(model, ChatGoogleGenerativeAI)

    def test_provider_alone_does_not_flip_judge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The §7.3 invariant: setting only RTIA_LLM_PROVIDER=ollama swaps
        # the GENERATOR; the judge stays on Gemini. The two switches are
        # independent.
        monkeypatch.setenv("RTIA_LLM_PROVIDER", "ollama")
        monkeypatch.delenv("RTIA_OLLAMA_JUDGE", raising=False)
        from evals.judge import GeminiJudge

        judge = GeminiJudge()
        model = judge.load_model()
        assert isinstance(model, ChatGoogleGenerativeAI)

    def test_ollama_judge_optin_returns_chat_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RTIA_OLLAMA_JUDGE", "1")
        from evals.judge import GeminiJudge

        judge = GeminiJudge()
        model = judge.load_model()
        # Don't import ChatOllama at module top because the optional dep
        # might not be installed in some environments; do the import here
        # alongside the assertion.
        from langchain_ollama import ChatOllama

        assert isinstance(model, ChatOllama)

    def test_get_model_name_reflects_ollama_judge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RTIA_OLLAMA_JUDGE", "1")
        monkeypatch.setenv("RTIA_OLLAMA_JUDGE_MODEL", "qwen2.5:14b")
        from evals.judge import GeminiJudge

        judge = GeminiJudge()
        assert judge.get_model_name() == "ollama:qwen2.5:14b"

    def test_get_model_name_default_is_gemini(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RTIA_OLLAMA_JUDGE", raising=False)
        from evals.judge import GeminiJudge

        judge = GeminiJudge(model="gemini-3.5-flash")
        assert judge.get_model_name() == "gemini-3.5-flash"

    def test_ollama_judge_default_model_is_default_ollama_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agents.config import DEFAULT_OLLAMA_MODEL

        monkeypatch.setenv("RTIA_OLLAMA_JUDGE", "1")
        monkeypatch.delenv("RTIA_OLLAMA_JUDGE_MODEL", raising=False)
        from evals.judge import GeminiJudge

        judge = GeminiJudge()
        assert judge.get_model_name() == f"ollama:{DEFAULT_OLLAMA_MODEL}"
