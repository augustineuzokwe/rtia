"""Load sample requirements and their per-agent ground truth blocks.

The on-disk format is defined by `evals/EVAL_DATA_SPEC.md` and enforced
structurally by `evals/validate_samples.py`. This module turns the
markdown files into typed Python objects the eval runner can consume,
without re-validating structure (the validator owns that).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SAMPLES_DIR = Path(__file__).parent / "sample-requirements"

_RAW_REQ_HEADER = "## Raw Requirement"
_ANALYST_HEADER = "## Expected Analyst Output"
_INTENT_HEADER = "### Intent"
_ACTORS_HEADER = "### Actors (expected set)"
_AMBIGUITY_HEADER = "### Ambiguity Categories"
_IMPLIED_HEADER = "### Implied Stories"
_AC_HEADER = "## Expected Acceptance Criteria"
_AC_CATEGORIES_HEADER = "### Required AC Categories"
_AC_COUNT_HEADER = "### Expected AC Count"
_AC_OUT_OF_SCOPE_HEADER = "### Out-of-Scope Behaviours"
_AC_COUNT_RE = re.compile(r"\b(\d+)\s*\(\s*±\s*(\d+)\s*\)")

_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$", re.MULTILINE)
_NONE_EXPECTED_RE = re.compile(r"\(none expected\)", re.IGNORECASE)
# Bold-label prefix used in sample-02/03 ambiguity bullets: "- **actor scoping** — …".
_BOLD_PREFIX_RE = re.compile(r"^\*\*(?P<label>[^*]+)\*\*\s*[—\-:]+\s*(?P<rest>.*)$")


@dataclass(frozen=True)
class ExpectedAnalystOutput:
    """Per-agent ground truth for the Requirements Analyst.

    Phrasing is illustrative — metrics compare fuzzily (judge for intent,
    set semantics for actors, category coverage for ambiguities/implied
    stories), never by exact string match.
    """

    intent: str
    actors: list[str]
    ambiguity_categories: list[str]
    """Short category labels (e.g. "actor scoping"). Empty list when the
    sample says "(none expected)"."""
    implied_story_titles: list[str]
    """Short story titles. Empty list when the sample says "(none expected)"."""


@dataclass(frozen=True)
class ExpectedAcceptanceCriteria:
    """Per-agent ground truth for the AC Generator.

    ``required_categories`` and ``out_of_scope`` are short labels (not
    full ACs) — eval metrics check that each required category is covered
    by *some* generated AC, and that no AC covers an out-of-scope item.
    ``expected_count`` carries the (count, tolerance) pair so the metric
    can penalise both under-coverage and over-flagging.
    """

    required_categories: list[str]
    expected_count: int
    count_tolerance: int
    out_of_scope: list[str]


@dataclass(frozen=True)
class SampleRecord:
    """A loaded sample-requirements file."""

    name: str
    """Filename stem, e.g. "sample-01-well-structured"."""
    path: Path
    raw_requirement: str
    expected_analyst: ExpectedAnalystOutput
    expected_acs: ExpectedAcceptanceCriteria
    extra: dict[str, str] = field(default_factory=dict)
    """Reserved for future per-agent ground-truth blocks (Test Case agent,
    Reviewer agent, …) once those agents land."""


def _extract_section(content: str, start_header: str, stop_headers: list[str]) -> str:
    """Return the body between `start_header` and the first stop header.

    Mirrors `validate_samples.extract_section` but raises if the section is
    absent — the validator runs first in CI and guarantees presence, so a
    missing section here is a contract violation worth failing loudly on.

    Stop headers are anchored to line start (matched as ``\\n<header>``) so a
    stop like ``## `` does not trigger inside ``### Intent`` (whose char-2 is
    also ``## ``).
    """
    start_idx = content.find(start_header)
    if start_idx == -1:
        raise ValueError(f"missing section: {start_header!r}")
    body_start = start_idx + len(start_header)
    earliest_stop = len(content)
    for stop in stop_headers:
        idx = content.find("\n" + stop, body_start)
        if idx != -1 and idx < earliest_stop:
            earliest_stop = idx
    return content[body_start:earliest_stop].strip()


def _strip_blockquote(text: str) -> str:
    """Remove '>'-prefixed guidance lines so the body matches what the LLM saw."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(">")
    ).strip()


def _bullets(section: str) -> list[str]:
    if _NONE_EXPECTED_RE.search(section):
        return []
    return [m.group("body").strip() for m in _BULLET_RE.finditer(section)]


def _bullet_label(bullet: str) -> str:
    """Normalise a bullet down to a short category/title label.

    Sample bullets are written as either plain text ("QA team member") or as
    a bold-prefixed pair ("**actor scoping** — are 'managers' and 'the team'…").
    We keep just the label so set comparisons stay legible; the long form is
    irrelevant to scoring.
    """
    match = _BOLD_PREFIX_RE.match(bullet)
    if match:
        return match.group("label").strip()
    # Some implied-story bullets use "Title — summary" without bold markers.
    if " — " in bullet:
        return bullet.split(" — ", 1)[0].strip()
    return bullet.strip()


def _parse_expected_analyst(content: str) -> ExpectedAnalystOutput:
    analyst_block = _extract_section(content, _ANALYST_HEADER, ["## "])
    intent_body = _strip_blockquote(_extract_section(analyst_block, _INTENT_HEADER, ["###", "## "]))
    actors_body = _extract_section(analyst_block, _ACTORS_HEADER, ["###", "## "])
    ambig_body = _extract_section(analyst_block, _AMBIGUITY_HEADER, ["###", "## "])
    implied_body = _extract_section(analyst_block, _IMPLIED_HEADER, ["###", "## "])

    return ExpectedAnalystOutput(
        intent=intent_body,
        actors=[_bullet_label(b) for b in _bullets(actors_body)],
        ambiguity_categories=[_bullet_label(b) for b in _bullets(ambig_body)],
        implied_story_titles=[_bullet_label(b) for b in _bullets(implied_body)],
    )


def _parse_expected_acs(content: str) -> ExpectedAcceptanceCriteria:
    ac_block = _extract_section(content, _AC_HEADER, ["## "])
    categories_body = _extract_section(ac_block, _AC_CATEGORIES_HEADER, ["###", "## "])
    count_body = _extract_section(ac_block, _AC_COUNT_HEADER, ["###", "## "])
    out_of_scope_body = _extract_section(ac_block, _AC_OUT_OF_SCOPE_HEADER, ["###", "## "])

    match = _AC_COUNT_RE.search(count_body)
    if match is None:
        raise ValueError(
            f"Expected AC Count block must contain a 'N (±M)' pattern; got: {count_body!r}"
        )
    expected_count, tolerance = int(match.group(1)), int(match.group(2))

    return ExpectedAcceptanceCriteria(
        required_categories=[_bullet_label(b) for b in _bullets(categories_body)],
        expected_count=expected_count,
        count_tolerance=tolerance,
        out_of_scope=[_bullet_label(b) for b in _bullets(out_of_scope_body)],
    )


def load_sample(path: Path) -> SampleRecord:
    """Parse a single sample-requirements file into a SampleRecord."""
    content = path.read_text(encoding="utf-8")
    raw = _strip_blockquote(_extract_section(content, _RAW_REQ_HEADER, ["## "]))
    return SampleRecord(
        name=path.stem,
        path=path,
        raw_requirement=raw,
        expected_analyst=_parse_expected_analyst(content),
        expected_acs=_parse_expected_acs(content),
    )


def load_all_samples(samples_dir: Path = SAMPLES_DIR) -> list[SampleRecord]:
    """Load every `sample-*.md` under `samples_dir`, sorted by filename."""
    return [load_sample(p) for p in sorted(samples_dir.glob("sample-*.md"))]
