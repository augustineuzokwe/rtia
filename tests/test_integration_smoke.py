"""Unit tests for the non-LLM logic of scripts/run_integration_smoke.

The script's job is to (a) call the agents, (b) check invariants on the
output, (c) sum token usage, and (d) enforce a budget. (a) needs live
LLM calls and lives in the workflow; (b)/(c)/(d) are checkable offline
and matter for catching regressions in the smoke gate itself.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from agents.final_artifact import FinalUserStory
from agents.requirements_analyst import Ambiguity, AnalystOutput


def _load_smoke_module():
    """Import the script as a module so we can unit-test its helpers."""
    spec = importlib.util.spec_from_file_location(
        "rtia_smoke",
        Path(__file__).resolve().parent.parent / "scripts" / "run_integration_smoke.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register in sys.modules BEFORE exec so @dataclass can resolve the
    # module's __dict__ during class processing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def test_auto_po_answers_only_resolves_critical() -> None:
    """Normal-severity ambiguities should flow through unanswered."""
    analyst = AnalystOutput(
        intent="x",
        actors=["A"],
        ambiguities=[
            Ambiguity(question="Q1?", severity="critical"),
            Ambiguity(question="Q2?", severity="normal"),
        ],
    )
    answers = smoke._auto_po_answers(analyst)
    assert list(answers) == ["Q1?"]
    assert "Auto-resolved" in answers["Q1?"]


def test_check_invariants_passes_on_well_formed_story() -> None:
    story = FinalUserStory(
        description="As a tester, I want X, so that Y.",
        objective="So that Y.",
        assumptions=[],
    )
    failures = smoke._check_invariants("sample-01-well-structured", story)
    assert failures == []


def test_check_invariants_flags_empty_description() -> None:
    story = FinalUserStory(description="   ", objective="O", assumptions=["a"])
    failures = smoke._check_invariants("sample-02-vague-ambiguous", story)
    assert any("description is empty" in f for f in failures)


def test_check_invariants_flags_bad_description_shape() -> None:
    story = FinalUserStory(
        description="The tester does the thing.",
        objective="O",
        assumptions=["a"],
    )
    failures = smoke._check_invariants("sample-02-vague-ambiguous", story)
    assert any("does not start with 'As a/an" in f for f in failures)


def test_check_invariants_flags_missing_assumptions_on_vague_sample() -> None:
    story = FinalUserStory(
        description="As a manager, I want visibility, so that I know.",
        objective="So that I know.",
        assumptions=[],
    )
    failures = smoke._check_invariants("sample-02-vague-ambiguous", story)
    assert any("at least one assumption" in f for f in failures)


def test_check_invariants_allows_empty_assumptions_on_well_structured_sample() -> None:
    story = FinalUserStory(
        description="As a QA Lead, I want X, so that Y.",
        objective="So that Y.",
        assumptions=[],
    )
    failures = smoke._check_invariants("sample-01-well-structured", story)
    assert failures == []


def test_strip_json_fences_handles_json_tagged_fence() -> None:
    assert smoke._strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_json_fences_handles_plain_fence() -> None:
    assert smoke._strip_json_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_json_fences_passes_unfenced_through() -> None:
    assert smoke._strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_usage_telemetry_add_accumulates() -> None:
    total = smoke.UsageTelemetry()
    total.add(smoke.UsageTelemetry(input_tokens=10, output_tokens=2))
    total.add(smoke.UsageTelemetry(input_tokens=5, output_tokens=3))
    assert total.input_tokens == 15
    assert total.output_tokens == 5
