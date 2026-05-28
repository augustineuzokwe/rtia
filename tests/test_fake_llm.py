"""Tests for the fake-LLM provider (ADR-0015).

Verifies the three concerns owned by US-34:

- ``use_fake()`` reads ``RTIA_LLM_PROVIDER`` correctly and is mutually
  exclusive with ``use_ollama()`` / the Google default.
- An invalid ``RTIA_LLM_PROVIDER`` value fails fast with a ``ValueError``
  naming the valid set (behaviour change from v1.0.0; silent fallback
  to Google was a silent footgun for typos).
- ``FakeChatModel.invoke`` returns the canned fixture JSON for the
  ``(scenario, agent_name)`` pair; missing files raise a clear error.
- ``RTIA_FAKE_SCENARIO`` routes to the matching directory; an unknown
  scenario raises ``ValueError``.

Pipeline-level smoke (``RTIA_LLM_PROVIDER=fake`` end-to-end through all
five agents) is exercised separately in :mod:`tests.test_graph` once
US-37 ports a graph test - this file stays focused on the provider
contract itself.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents import _fake_llm
from agents._fake_llm import (
    DEFAULT_FAKE_SCENARIO,
    FAKE_SCENARIO_ENV_VAR,
    VALID_SCENARIOS,
    FakeChatModel,
    current_scenario,
)
from agents.config import (
    LLM_PROVIDER_ENV_VAR,
    _llm_provider,
    use_fake,
    use_ollama,
)
from agents.requirements_analyst import AnalystOutput


def test_use_fake_true_when_provider_is_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_PROVIDER_ENV_VAR, "fake")
    assert use_fake() is True
    assert use_ollama() is False


def test_use_fake_false_when_provider_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LLM_PROVIDER_ENV_VAR, raising=False)
    assert use_fake() is False
    assert use_ollama() is False
    # Default still routes to google.
    assert _llm_provider() == "google"


def test_use_fake_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_PROVIDER_ENV_VAR, "Fake")
    assert use_fake() is True


def test_invalid_provider_raises_clear_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LLM_PROVIDER_ENV_VAR, "anthropic")
    with pytest.raises(ValueError) as excinfo:
        _llm_provider()
    msg = str(excinfo.value)
    assert "anthropic" in msg
    assert "valid values" in msg.lower()
    # All three valid values are named so a developer can fix the typo
    # without re-reading the source.
    for value in ("google", "ollama", "fake"):
        assert value in msg


def test_current_scenario_defaults_to_deep_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FAKE_SCENARIO_ENV_VAR, raising=False)
    assert current_scenario() == DEFAULT_FAKE_SCENARIO == "deep_clean"


def test_current_scenario_validates_known_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "bogus_typo")
    with pytest.raises(ValueError) as excinfo:
        current_scenario()
    msg = str(excinfo.value)
    assert "bogus_typo" in msg
    # Every valid scenario appears in the error so the fix is obvious.
    for scenario in VALID_SCENARIOS:
        assert scenario in msg


def test_fake_chat_model_returns_canned_fixture_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "deep_clean")
    llm = FakeChatModel(agent_name="requirements_analyst")
    response = llm.invoke(messages=["unused"], config=None)
    # AIMessage.content is the raw fixture text. Downstream agents
    # parse it via strip_json_fence + Pydantic; we replicate the parse
    # here to prove the fixture is wire-compatible with the schema.
    parsed = AnalystOutput.model_validate(json.loads(response.content))
    assert parsed.intent
    assert parsed.actors  # at least one actor in deep_clean
    assert parsed.ambiguities == []  # deep_clean has no ambiguities
    assert parsed.implied_stories == []  # deep_clean is single-story
    assert parsed.suspicious_input.detected is False


def test_fake_chat_model_missing_fixture_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "deep_clean")
    # Point the fixture root at an empty temp dir to simulate missing files.
    monkeypatch.setattr(_fake_llm, "_fixture_root", lambda: tmp_path)
    llm = FakeChatModel(agent_name="requirements_analyst")
    with pytest.raises(FileNotFoundError) as excinfo:
        llm.invoke(messages=[], config=None)
    msg = str(excinfo.value)
    # Both the scenario and the agent name are named so a developer can
    # diagnose without grepping the source.
    assert "deep_clean" in msg
    assert "requirements_analyst" in msg


def test_fake_chat_model_error_scenario_raises_for_analyst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "error")
    llm = FakeChatModel(agent_name="requirements_analyst")
    with pytest.raises(RuntimeError) as excinfo:
        llm.invoke(messages=[], config=None)
    # The message must say "error" so a debugger sees this is intentional,
    # not a real LLM failure.
    assert "error" in str(excinfo.value).lower()


def test_all_deep_clean_fixtures_validate_against_their_schemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every fixture in the deep_clean scenario must parse cleanly.

    Drift between Pydantic schemas and fixtures would otherwise show up
    only when the relevant agent runs end-to-end. This test catches it
    at PR review time.
    """
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "deep_clean")
    # Lazy import so test fixtures aren't a hard dep at module load time.
    from agents.ac_generator import AcGeneratorOutput
    from agents.reviewer import ReviewReport
    from agents.test_case_writer import TestCaseWriterOutput
    from agents.user_story_writer import UserStory

    fixtures_to_schemas: dict[str, type] = {
        "requirements_analyst": AnalystOutput,
        "user_story_writer": UserStory,
        "ac_generator": AcGeneratorOutput,
        "test_case_writer": TestCaseWriterOutput,
        "reviewer": ReviewReport,
    }
    for agent_name, schema in fixtures_to_schemas.items():
        llm = FakeChatModel(agent_name=agent_name)
        response = llm.invoke(messages=[], config=None)
        try:
            schema.model_validate(json.loads(response.content))
        except ValidationError as exc:
            pytest.fail(
                f"deep_clean fixture for {agent_name} does not match {schema.__name__}: {exc}"
            )
