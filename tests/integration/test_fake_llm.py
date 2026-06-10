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


def _schemas_by_agent() -> dict[str, type]:
    """Lazy import so test fixtures aren't a hard dep at module-load time."""
    from agents.ac_generator import AcGeneratorOutput
    from agents.reviewer import ReviewReport
    from agents.test_case_writer import TestCaseWriterOutput
    from agents.user_story_writer import UserStory

    return {
        "requirements_analyst": AnalystOutput,
        "user_story_writer": UserStory,
        "ac_generator": AcGeneratorOutput,
        "test_case_writer": TestCaseWriterOutput,
        "reviewer": ReviewReport,
    }


# Per-scenario agent coverage. Drives the schema-validation test below.
# Adding a new scenario: append it here AND in VALID_SCENARIOS.
#
# - deep_clean / deep_with_po: full pipeline runs; every agent has a fixture.
# - split: terminates at split_node (pure Python) after the Analyst; only the
#   Analyst fixture is consumed.
# - error: FakeChatModel raises in the Analyst before any file read; no
#   fixtures are loaded for any agent in this scenario.
_SCENARIO_AGENT_COVERAGE: dict[str, list[str]] = {
    "deep_clean": [
        "requirements_analyst",
        "user_story_writer",
        "ac_generator",
        "test_case_writer",
        "reviewer",
    ],
    "deep_with_po": [
        "requirements_analyst",
        "user_story_writer",
        "ac_generator",
        "test_case_writer",
        "reviewer",
    ],
    "split": ["requirements_analyst"],
    "error": [],
}


@pytest.mark.parametrize("scenario", sorted(VALID_SCENARIOS))
def test_every_scenario_in_coverage_matrix(scenario: str) -> None:
    """The agent-coverage matrix above must list every VALID_SCENARIO.

    Catches the case where a new scenario lands in :data:`VALID_SCENARIOS`
    without a matching entry here - the schema validation test would then
    silently skip it.
    """
    assert scenario in _SCENARIO_AGENT_COVERAGE, (
        f"Scenario {scenario!r} is in VALID_SCENARIOS but not in "
        "_SCENARIO_AGENT_COVERAGE. Update the matrix in tests/test_fake_llm.py."
    )


@pytest.mark.parametrize("scenario", sorted(VALID_SCENARIOS))
def test_all_scenario_fixtures_validate_against_their_schemas(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Every fixture in every scenario must parse cleanly against its schema.

    Drift between Pydantic schemas and fixtures would otherwise only show
    up when the relevant agent runs end-to-end. This test catches it at
    PR review time. Parameterised so each scenario is its own assertion.
    """
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, scenario)
    schemas = _schemas_by_agent()
    for agent_name in _SCENARIO_AGENT_COVERAGE[scenario]:
        llm = FakeChatModel(agent_name=agent_name)
        response = llm.invoke(messages=[], config=None)
        schema = schemas[agent_name]
        try:
            schema.model_validate(json.loads(response.content))
        except ValidationError as exc:
            pytest.fail(
                f"Fixture {scenario}/{agent_name}.json does not match {schema.__name__}: {exc}"
            )


def test_split_scenario_has_two_or_more_implied_stories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The split scenario only triggers the split path when ``implied_stories ≥ 2``.

    A drift in the fixture (e.g. someone trims to one story) would silently
    re-route the test through the deep path. Pin the contract here.
    """
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "split")
    llm = FakeChatModel(agent_name="requirements_analyst")
    response = llm.invoke(messages=[], config=None)
    parsed = AnalystOutput.model_validate(json.loads(response.content))
    assert len(parsed.implied_stories) >= 2, (
        "split scenario fixture must have ≥2 implied_stories to route through "
        "split_node; got "
        f"{len(parsed.implied_stories)}."
    )


def test_deep_with_po_has_at_least_one_critical_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``deep_with_po`` only pauses the PO checkpoint when at least one
    critical ambiguity is present. Pin the contract.
    """
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "deep_with_po")
    llm = FakeChatModel(agent_name="requirements_analyst")
    response = llm.invoke(messages=[], config=None)
    parsed = AnalystOutput.model_validate(json.loads(response.content))
    critical = [a for a in parsed.ambiguities if a.severity == "critical"]
    assert critical, (
        "deep_with_po scenario fixture must include ≥1 critical ambiguity to "
        "pause the PO checkpoint."
    )


def test_deep_clean_has_no_critical_ambiguities_and_no_implied_stories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``deep_clean`` must flow straight through the PO checkpoint without
    pausing AND must not trigger the split path. This is the happiest path.
    """
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "deep_clean")
    llm = FakeChatModel(agent_name="requirements_analyst")
    response = llm.invoke(messages=[], config=None)
    parsed = AnalystOutput.model_validate(json.loads(response.content))
    critical = [a for a in parsed.ambiguities if a.severity == "critical"]
    assert critical == []
    assert parsed.implied_stories == []


def test_scenario_is_read_per_invoke_not_per_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single FakeChatModel instance must honour a mid-process change
    to RTIA_FAKE_SCENARIO. The Playwright suite relies on this to drive
    one thread through multiple states without rebuilding the agent.

    Documented as a deliberate design choice in agents/_fake_llm.py and
    USAGE.md §11. If this test fails, look for accidental caching of
    the scenario at __init__ time.
    """
    llm = FakeChatModel(agent_name="requirements_analyst")

    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "deep_clean")
    clean = AnalystOutput.model_validate(json.loads(llm.invoke([]).content))
    assert clean.implied_stories == []

    # Same instance, different env → different fixture loaded.
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "split")
    split = AnalystOutput.model_validate(json.loads(llm.invoke([]).content))
    assert len(split.implied_stories) >= 2

    # Same instance again, error scenario → controlled raise.
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "error")
    with pytest.raises(RuntimeError):
        llm.invoke([])


def test_non_analyst_agents_succeed_under_error_scenario_if_fixture_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The ``error`` raise is scoped to ``requirements_analyst``.

    Verifying this so a future maintainer doesn't accidentally generalise
    the raise to "any agent in the error scenario" and break tests that
    target a downstream-agent failure. If you ever DO want a non-Analyst
    agent to fail under ``error``, add a dedicated scenario for it
    rather than overloading this one.
    """
    monkeypatch.setenv(FAKE_SCENARIO_ENV_VAR, "error")
    # Point the fake at a temp dir holding a minimal user_story_writer
    # fixture so we can prove the non-analyst path doesn't raise.
    fake_root = tmp_path / "fake"
    (fake_root / "error").mkdir(parents=True)
    (fake_root / "error" / "user_story_writer.json").write_text(
        json.dumps(
            {
                "description": "As a test user, I want a story.",
                "objective": "I can verify the error scenario only raises for analyst.",
                "assumptions": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_fake_llm, "_fixture_root", lambda: fake_root)

    llm = FakeChatModel(agent_name="user_story_writer")
    response = llm.invoke([])
    # No raise, content is the canned fixture.
    assert "test user" in response.content
