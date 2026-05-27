"""Tests for the per-sample PO directive fixture loader in evals.run_evals.

The fixtures themselves (``evals/ground-truth/po-answers/*.yaml``) carry
scope decisions the eval runner pins to AC ground truth - these tests
guard the loader contract:

- Present, well-formed fixture → returns the directive string.
- Missing fixture → returns ``None`` (runner falls back to the constant).
- Present-but-empty / malformed fixture → returns ``None`` (no silent half-state).
- ``_auto_po_answers`` applies the resolved answer to every CRITICAL
  ambiguity only - normal-severity ambiguities are not auto-answered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.requirements_analyst import Ambiguity, AnalystOutput
from evals.run_evals import (
    _AUTO_PO_ANSWER,
    _auto_po_answers,
    _load_po_directive,
)


def _ao(*ambig: Ambiguity) -> AnalystOutput:
    return AnalystOutput(
        intent="x",
        actors=["a"],
        ambiguities=list(ambig),
        implied_stories=[],
    )


def test_load_po_directive_returns_string_for_known_sample() -> None:
    directive = _load_po_directive("sample-02-vague-ambiguous")
    assert isinstance(directive, str)
    assert "scope" in directive.lower()
    assert "defect" in directive.lower()


def test_load_po_directive_returns_none_when_fixture_absent() -> None:
    assert _load_po_directive("sample-99-does-not-exist") is None


def test_load_po_directive_returns_none_for_empty_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "sample-xx.yaml"
    fixture.write_text("po_directive: ''\n", encoding="utf-8")
    monkeypatch.setattr("evals.run_evals.PO_DIRECTIVES_DIR", tmp_path)
    assert _load_po_directive("sample-xx") is None


def test_load_po_directive_returns_none_for_wrong_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "sample-yy.yaml"
    fixture.write_text("po_directive: [not, a, string]\n", encoding="utf-8")
    monkeypatch.setattr("evals.run_evals.PO_DIRECTIVES_DIR", tmp_path)
    assert _load_po_directive("sample-yy") is None


def test_auto_po_answers_uses_directive_for_sample_with_fixture() -> None:
    ao = _ao(
        Ambiguity(question="Q1?", severity="critical"),
        Ambiguity(question="Q2?", severity="critical"),
        Ambiguity(question="Q3?", severity="normal"),
    )
    answers = _auto_po_answers("sample-02-vague-ambiguous", ao)
    assert set(answers.keys()) == {"Q1?", "Q2?"}
    # Normal ambiguity is not auto-answered.
    assert "Q3?" not in answers
    # Both critical questions get the same directive (not the fallback).
    distinct = set(answers.values())
    assert len(distinct) == 1
    assert _AUTO_PO_ANSWER not in distinct
    assert "scope" in next(iter(distinct)).lower()


def test_auto_po_answers_falls_back_to_constant_when_no_fixture() -> None:
    ao = _ao(Ambiguity(question="Q1?", severity="critical"))
    answers = _auto_po_answers("sample-99-no-fixture", ao)
    assert answers == {"Q1?": _AUTO_PO_ANSWER}


def test_auto_po_answers_empty_when_no_critical_ambiguities() -> None:
    ao = _ao(Ambiguity(question="Q1?", severity="normal"))
    answers = _auto_po_answers("sample-02-vague-ambiguous", ao)
    assert answers == {}
