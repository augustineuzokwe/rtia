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
_INTENT_KEY_TERMS_HEADER = "### Intent Key Terms"
_REQUIREMENT_KEY_TERMS_HEADER = "## Requirement Key Terms"
_ACTORS_HEADER = "### Actors (expected set)"
_AMBIGUITY_HEADER = "### Ambiguity Categories"
_IMPLIED_HEADER = "### Implied Stories"
_AC_HEADER = "## Expected Acceptance Criteria"
_AC_CATEGORIES_HEADER = "### Required AC Categories"
_AC_COUNT_HEADER = "### Expected AC Count"
_AC_OUT_OF_SCOPE_HEADER = "### Out-of-Scope Behaviours"
_AC_COUNT_RE = re.compile(r"\b(\d+)\s*\(\s*±\s*(\d+)\s*\)")
_INJECTION_HEADER = "## Injection Test"
_INJECTION_TYPE_HEADER = "### Injection Type"
_INJECTION_VECTOR_HEADER = "### Injection Vector"
_INJECTION_FORBIDDEN_HEADER = "### Forbidden Patterns"
_INJECTION_BEHAVIOR_HEADER = "### Expected Pipeline Behavior"
_DETECTED_TRUE_RE = re.compile(r"`?suspicious_input\.detected`?\s*==\s*true", re.IGNORECASE)
_DETECTED_FALSE_RE = re.compile(r"`?suspicious_input\.detected`?\s*==\s*false", re.IGNORECASE)

_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<body>.+?)\s*$", re.MULTILINE)
_NONE_EXPECTED_RE = re.compile(r"\(none expected\)", re.IGNORECASE)
# Bold-label prefix used in sample-02/03 ambiguity bullets: "- **actor scoping** - …".
_BOLD_PREFIX_RE = re.compile(r"^\*\*(?P<label>[^*]+)\*\*\s*[-\-:]+\s*(?P<rest>.*)$")


@dataclass(frozen=True)
class ExpectedAnalystOutput:
    """Per-agent ground truth for the Requirements Analyst.

    Phrasing is illustrative - metrics compare fuzzily (judge for intent,
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
    intent_key_terms: list[str] = field(default_factory=list)
    """Load-bearing domain phrases the Analyst's intent string must mention
    (case-insensitive substring match). Hand-curated per sample because
    token-overlap against the full prose intent is too coarse - too many
    interchangeable verbs ("monitor"/"view", "let"/"enable") collapse the
    score. Substring match on a small set of *named* domain terms
    survives Analyst paraphrasing. See ``score_intent_keyword_overlap``.
    Empty list when no per-sample key terms are pinned yet (metric scores
    0.0 with a clear reason in that case, surfacing the gap)."""


@dataclass(frozen=True)
class ExpectedAcceptanceCriteria:
    """Per-agent ground truth for the AC Generator.

    ``required_categories`` and ``out_of_scope`` are short labels (not
    full ACs) - eval metrics check that each required category is covered
    by *some* generated AC, and that no AC covers an out-of-scope item.
    ``expected_count`` carries the (count, tolerance) pair so the metric
    can penalise both under-coverage and over-flagging.
    """

    required_categories: list[str]
    expected_count: int
    count_tolerance: int
    out_of_scope: list[str]


@dataclass(frozen=True)
class InjectionTest:
    """Per-sample ground truth for the prompt-injection resistance metric.

    Populated from the optional ``## Injection Test`` block in the sample
    file (see ``EVAL_DATA_SPEC.md`` §Rules for Injection Test).

    - ``injection_type`` and ``injection_vector`` are short free-form
      labels carried into the eval report for failure-mode tagging; they
      are not used by the metric for scoring.
    - ``forbidden_patterns`` are case-insensitive substrings that MUST NOT
      appear anywhere in the composite final artifact. Any one match
      collapses ``score_injection_resistance`` to 0.0.
    - ``expected_detected`` carries the parsed expectation for
      ``AnalystOutput.suspicious_input.detected`` - True for adversarial
      samples (04, 05, 06), False for the negative anchor (sample-07).
      The metric asserts the Analyst's actual flag matches this value.
    """

    injection_type: str
    injection_vector: str
    forbidden_patterns: list[str]
    expected_detected: bool


@dataclass(frozen=True)
class SampleRecord:
    """A loaded sample-requirements file."""

    name: str
    """Filename stem, e.g. "sample-01-well-structured"."""
    path: Path
    raw_requirement: str
    expected_analyst: ExpectedAnalystOutput
    expected_acs: ExpectedAcceptanceCriteria
    requirement_key_terms: list[str] = field(default_factory=list)
    """User-facing specifics from the raw requirement that MUST survive into
    the composite final artifact (description + objective + ACs + test
    cases). Consumed by ``score_requirement_fidelity``. Lives at the
    SampleRecord level rather than ``ExpectedAnalystOutput`` because it's
    artifact-wide ground truth, not per-agent. Empty list = no terms
    pinned yet (metric returns 0.0 with a diagnostic message)."""
    injection_test: InjectionTest | None = None
    """Set when the sample file contains a ``## Injection Test`` section,
    None otherwise. Non-None samples are scored by
    ``score_injection_resistance``; None samples skip that metric. Phase
    12.1."""
    extra: dict[str, str] = field(default_factory=dict)
    """Reserved for future per-agent ground-truth blocks (Test Case agent,
    Reviewer agent, …) once those agents land."""


def _extract_section(content: str, start_header: str, stop_headers: list[str]) -> str:
    """Return the body between `start_header` and the first stop header.

    Mirrors `validate_samples.extract_section` but raises if the section is
    absent - the validator runs first in CI and guarantees presence, so a
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
    a bold-prefixed pair ("**actor scoping** - are 'managers' and 'the team'…").
    We keep just the label so set comparisons stay legible; the long form is
    irrelevant to scoring.
    """
    match = _BOLD_PREFIX_RE.match(bullet)
    if match:
        return match.group("label").strip()
    # Some implied-story bullets use "Title - summary" without bold markers.
    if " - " in bullet:
        return bullet.split(" - ", 1)[0].strip()
    return bullet.strip()


def _parse_expected_analyst(content: str) -> ExpectedAnalystOutput:
    analyst_block = _extract_section(content, _ANALYST_HEADER, ["## "])
    intent_body = _strip_blockquote(_extract_section(analyst_block, _INTENT_HEADER, ["###", "## "]))
    actors_body = _extract_section(analyst_block, _ACTORS_HEADER, ["###", "## "])
    ambig_body = _extract_section(analyst_block, _AMBIGUITY_HEADER, ["###", "## "])
    implied_body = _extract_section(analyst_block, _IMPLIED_HEADER, ["###", "## "])
    # Intent Key Terms is an OPTIONAL section. When absent, the list
    # stays empty and the metric consumer scores zero with a diagnostic
    # message - not silent ignore. We don't use _extract_section here
    # because it raises on missing sections by design.
    if _INTENT_KEY_TERMS_HEADER in analyst_block:
        key_terms_body = _extract_section(analyst_block, _INTENT_KEY_TERMS_HEADER, ["###", "## "])
    else:
        key_terms_body = ""

    return ExpectedAnalystOutput(
        intent=intent_body,
        actors=[_bullet_label(b) for b in _bullets(actors_body)],
        ambiguity_categories=[_bullet_label(b) for b in _bullets(ambig_body)],
        implied_story_titles=[_bullet_label(b) for b in _bullets(implied_body)],
        intent_key_terms=[_bullet_label(b) for b in _bullets(key_terms_body)],
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


def _parse_injection_test(content: str) -> InjectionTest | None:
    """Parse the optional ``## Injection Test`` block.

    Returns None when the section is absent - the metric skips samples
    without this block. When present, all four sub-sections must parse
    cleanly; a malformed block raises rather than silently disabling the
    security check.
    """
    # Anchor to start-of-line via a leading "\n" - the literal string
    # `## Injection Test` also appears in backtick prose inside the
    # Expected Analyst Output blockquote of every adversarial sample, and
    # an unanchored `.find` would match that first and return the wrong
    # block.
    anchored = "\n" + _INJECTION_HEADER
    if anchored not in content:
        return None
    block = _extract_section(content, _INJECTION_HEADER, ["## "])
    # If the first match was an in-prose mention (no real header follows),
    # _extract_section may have returned a block without our sub-headers.
    # Re-anchor: slice from the anchored newline match forward.
    if _INJECTION_TYPE_HEADER not in block:
        anchored_idx = content.find(anchored)
        # +1 to skip the leading newline, header parsing then proceeds
        # exactly as before but starting from the real header.
        block_start = anchored_idx + 1
        # Reuse _extract_section by slicing first.
        sub_content = content[block_start:]
        block = _extract_section(sub_content, _INJECTION_HEADER, ["## "])
    type_body = _strip_blockquote(
        _extract_section(block, _INJECTION_TYPE_HEADER, ["###", "## "])
    ).strip()
    vector_body = _strip_blockquote(
        _extract_section(block, _INJECTION_VECTOR_HEADER, ["###", "## "])
    ).strip()
    forbidden_body = _extract_section(block, _INJECTION_FORBIDDEN_HEADER, ["###", "## "])
    behavior_body = _extract_section(block, _INJECTION_BEHAVIOR_HEADER, ["###", "## "])

    # Forbidden Patterns: bullets, OR a parenthetical "(none - ...)" marker
    # on the negative sample. The bullet parser treats the marker as zero
    # bullets, which is exactly what we want.
    forbidden_patterns = [b.strip() for b in _bullets(forbidden_body)]

    # Expected detected value: parsed from the behaviour bullets via regex
    # match on `suspicious_input.detected == true|false`. Adversarial
    # samples list this assertion explicitly; the negative sample lists
    # `== false`. A missing assertion is treated as a sample-file bug.
    if _DETECTED_TRUE_RE.search(behavior_body):
        expected_detected = True
    elif _DETECTED_FALSE_RE.search(behavior_body):
        expected_detected = False
    else:
        raise ValueError(
            "Injection Test → Expected Pipeline Behavior must include an "
            "assertion of the form 'suspicious_input.detected == true' or "
            "'... == false'."
        )

    return InjectionTest(
        injection_type=type_body,
        injection_vector=vector_body,
        forbidden_patterns=forbidden_patterns,
        expected_detected=expected_detected,
    )


def load_sample(path: Path) -> SampleRecord:
    """Parse a single sample-requirements file into a SampleRecord."""
    content = path.read_text(encoding="utf-8")
    raw = _strip_blockquote(_extract_section(content, _RAW_REQ_HEADER, ["## "]))
    # Requirement Key Terms is an OPTIONAL ## section. Same shape as
    # Intent Key Terms (optional, drives score_requirement_fidelity).
    if _REQUIREMENT_KEY_TERMS_HEADER in content:
        req_terms_body = _extract_section(content, _REQUIREMENT_KEY_TERMS_HEADER, ["## "])
        requirement_key_terms = [_bullet_label(b) for b in _bullets(req_terms_body)]
    else:
        requirement_key_terms = []
    return SampleRecord(
        name=path.stem,
        path=path,
        raw_requirement=raw,
        expected_analyst=_parse_expected_analyst(content),
        expected_acs=_parse_expected_acs(content),
        requirement_key_terms=requirement_key_terms,
        injection_test=_parse_injection_test(content),
    )


def load_all_samples(samples_dir: Path = SAMPLES_DIR) -> list[SampleRecord]:
    """Load every `sample-*.md` under `samples_dir`, sorted by filename."""
    return [load_sample(p) for p in sorted(samples_dir.glob("sample-*.md"))]
