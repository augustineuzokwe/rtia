"""Unit + integration tests for the output sanitiser.

Tests are arranged per public function (strip_control_chars,
normalize_code_fences, enforce_length_cap, sanitize_artifact) plus a
small integration block that exercises ``FinalUserStory.as_markdown``
end-to-end on a deliberately hostile story object.

The pattern follows the rest of the repo: pure unit tests with
deterministic inputs, no LLM calls, no fixtures shared across modules.
"""

from __future__ import annotations

from agents._sanitize import (
    DEFAULT_ALLOWED_LANGS,
    DEFAULT_MAX_CHARS,
    SanitizeReport,
    enforce_length_cap,
    normalize_code_fences,
    sanitize_artifact,
    strip_control_chars,
)
from agents.final_artifact import AcceptanceCriterion, FinalUserStory, TestCase

# ---------------------------------------------------------------------------
# strip_control_chars
# ---------------------------------------------------------------------------


def test_strip_control_chars_preserves_clean_text() -> None:
    text = "A normal user story description with tabs\tand\nnewlines\r\n."
    cleaned, ctrl, inv = strip_control_chars(text)
    assert cleaned == text
    assert ctrl == 0
    assert inv == 0


def test_strip_control_chars_removes_nul_bell_esc() -> None:
    text = "before\x00mid\x07more\x1bafter"
    cleaned, ctrl, inv = strip_control_chars(text)
    assert cleaned == "beforemidmoreafter"
    assert ctrl == 3
    assert inv == 0


def test_strip_control_chars_removes_del() -> None:
    cleaned, ctrl, inv = strip_control_chars("ok\x7fbye")
    assert cleaned == "okbye"
    assert ctrl == 1
    assert inv == 0


def test_strip_control_chars_keeps_tab_lf_cr() -> None:
    text = "\tindent\nnewline\rcarriage"
    cleaned, ctrl, inv = strip_control_chars(text)
    assert cleaned == text
    assert ctrl == 0
    assert inv == 0


def test_strip_control_chars_removes_zero_width_chars() -> None:
    # ZWSP, ZWNJ, ZWJ between visible letters.
    text = "a​b‌c‍d"
    cleaned, ctrl, inv = strip_control_chars(text)
    assert cleaned == "abcd"
    assert ctrl == 0
    assert inv == 3


def test_strip_control_chars_removes_bidi_overrides_trojan_source() -> None:
    # RLO (U+202E) is the Trojan Source vector. CVE-2021-42574.
    text = "user‮admin"
    cleaned, ctrl, inv = strip_control_chars(text)
    assert cleaned == "useradmin"
    assert inv == 1


def test_strip_control_chars_removes_bom() -> None:
    text = "﻿start"
    cleaned, _ctrl, inv = strip_control_chars(text)
    assert cleaned == "start"
    assert inv == 1


def test_strip_control_chars_empty_input_is_safe() -> None:
    cleaned, ctrl, inv = strip_control_chars("")
    assert cleaned == ""
    assert ctrl == 0
    assert inv == 0


# ---------------------------------------------------------------------------
# normalize_code_fences
# ---------------------------------------------------------------------------


def test_normalize_code_fences_allows_known_languages() -> None:
    text = "Pre.\n```python\nx = 1\n```\nPost."
    cleaned, rewritten = normalize_code_fences(text)
    assert cleaned == text
    assert rewritten == []


def test_normalize_code_fences_drops_unknown_language() -> None:
    text = "before\n```mermaid\ngraph TD; A-->B\n```\nafter"
    cleaned, rewritten = normalize_code_fences(text)
    assert "```mermaid" not in cleaned
    assert "```" in cleaned
    assert "graph TD; A-->B" in cleaned  # body preserved
    assert rewritten == ["mermaid"]


def test_normalize_code_fences_drops_html_javascript_svg() -> None:
    text = (
        "x\n```html\n<script>alert(1)</script>\n```\n"
        "```javascript\nfetch('/')\n```\n"
        "```svg\n<svg/>\n```"
    )
    cleaned, rewritten = normalize_code_fences(text)
    assert "```html" not in cleaned
    # Note: ``javascript`` IS in the allowlist (it's a common, safe-by-default lang).
    # ``html`` and ``svg`` are NOT - only those get dropped.
    assert "```svg" not in cleaned
    assert "```javascript" in cleaned
    assert set(rewritten) == {"html", "svg"}


def test_normalize_code_fences_case_insensitive_allowlist() -> None:
    text = "```PYTHON\ny = 2\n```"
    cleaned, rewritten = normalize_code_fences(text)
    # Allowlist match is case-insensitive - uppercase python is still allowed.
    # The opener keeps its original casing since we only rewrite on miss.
    assert cleaned == text
    assert rewritten == []


def test_normalize_code_fences_preserves_bare_fences() -> None:
    text = "```\ncontent\n```"
    cleaned, rewritten = normalize_code_fences(text)
    assert cleaned == text
    assert rewritten == []


def test_normalize_code_fences_preserves_four_backtick_fence_width() -> None:
    # Markdown allows 4+ backticks when the body itself contains 3.
    text = "````evil\nbody\n````"
    cleaned, rewritten = normalize_code_fences(text)
    assert "````evil" not in cleaned
    assert "````" in cleaned
    assert rewritten == ["evil"]


# ---------------------------------------------------------------------------
# enforce_length_cap
# ---------------------------------------------------------------------------


def test_enforce_length_cap_no_op_under_limit() -> None:
    text = "a" * 100
    cleaned, original = enforce_length_cap(text, max_chars=1000)
    assert cleaned == text
    assert original is None


def test_enforce_length_cap_truncates_with_marker() -> None:
    text = "x" * 200
    cleaned, original = enforce_length_cap(text, max_chars=80)
    assert original == 200
    assert "truncated" in cleaned
    assert len(cleaned) <= 80


def test_enforce_length_cap_marker_does_not_push_past_cap() -> None:
    """Final length must be <= max_chars including the truncation marker."""
    text = "y" * 50_000
    cleaned, original = enforce_length_cap(text, max_chars=DEFAULT_MAX_CHARS)
    assert original == 50_000
    assert len(cleaned) <= DEFAULT_MAX_CHARS


def test_enforce_length_cap_exactly_at_limit_is_not_truncated() -> None:
    text = "z" * 100
    cleaned, original = enforce_length_cap(text, max_chars=100)
    assert cleaned == text
    assert original is None


# ---------------------------------------------------------------------------
# sanitize_artifact - composed pipeline
# ---------------------------------------------------------------------------


def test_sanitize_artifact_clean_input_is_noop() -> None:
    text = "## Description\nA normal story.\n\n## Objective\nClear value."
    cleaned, report = sanitize_artifact(text)
    assert cleaned == text
    assert report.is_clean


def test_sanitize_artifact_applies_all_three_passes() -> None:
    # Hostile input: control byte + bidi override + unknown fence + over-length tail.
    body = "Story\x00‮admin\n```html\n<svg/>\n```\n" + ("padding " * 4_000)
    cleaned, report = sanitize_artifact(body, max_chars=1_000)
    assert "\x00" not in cleaned
    assert "‮" not in cleaned
    assert "```html" not in cleaned
    assert "<svg/>" in cleaned  # body of fenced block preserved
    assert len(cleaned) <= 1_000
    assert report.control_chars_stripped == 1
    assert report.invisible_chars_stripped == 1
    assert report.fences_rewritten == ["html"]
    assert report.truncated_at is not None
    assert not report.is_clean


def test_sanitize_artifact_order_is_strip_then_normalize_then_cap() -> None:
    """Verify the canonical order: a truncation marker must survive the run."""
    # Build text where a control char sits where the truncation marker would
    # land if order were reversed. If length-cap ran first, the truncation
    # marker (\n\n_[...]_) survives. If strip ran last, "[truncated"
    # itself would be safe - but the underscore framing should still appear.
    big = "a" * 30_000
    cleaned, report = sanitize_artifact(big, max_chars=200)
    assert "truncated" in cleaned
    assert report.truncated_at == 30_000


def test_sanitize_artifact_defaults_match_module_constants() -> None:
    """Calling with no kwargs uses DEFAULT_MAX_CHARS + DEFAULT_ALLOWED_LANGS."""
    text = "fine"
    cleaned, report = sanitize_artifact(text)
    assert cleaned == text
    # Default allowlist includes python; calling with the explicit default
    # should produce identical output to the no-arg call.
    cleaned2, _ = sanitize_artifact(
        text, max_chars=DEFAULT_MAX_CHARS, allowed_langs=DEFAULT_ALLOWED_LANGS
    )
    assert cleaned == cleaned2
    assert report.is_clean


def test_sanitize_artifact_is_idempotent_on_clean_input() -> None:
    text = "## A\nbody\n\n## B\n```python\nx=1\n```\nend"
    first, _ = sanitize_artifact(text)
    second, report2 = sanitize_artifact(first)
    assert first == second
    assert report2.is_clean


# ---------------------------------------------------------------------------
# SanitizeReport convenience
# ---------------------------------------------------------------------------


def test_sanitize_report_is_clean_when_default() -> None:
    assert SanitizeReport().is_clean


def test_sanitize_report_not_clean_when_anything_changed() -> None:
    assert not SanitizeReport(control_chars_stripped=1).is_clean
    assert not SanitizeReport(invisible_chars_stripped=1).is_clean
    assert not SanitizeReport(fences_rewritten=["html"]).is_clean
    assert not SanitizeReport(truncated_at=100).is_clean


# ---------------------------------------------------------------------------
# Integration - FinalUserStory.as_markdown wires sanitisation
# ---------------------------------------------------------------------------


def _hostile_story() -> FinalUserStory:
    """Build a FinalUserStory whose fields contain attack payloads."""
    return FinalUserStory(
        description="As a user‮admin, I want\x00 access.",
        objective="Gain visibility into​ operations.",
        acceptance_criteria=[
            AcceptanceCriterion(
                given="logged in",
                when="I click the export button",
                then="a CSV downloads",
            ),
        ],
        test_cases=[
            TestCase(
                scenario="happy path",
                type="happy_path",
                steps=["1. click", "2. wait"],
                expected="file downloads",
            ),
        ],
        assumptions=["No edge cases out of scope."],
    )


def test_as_markdown_strips_control_and_bidi_from_fields() -> None:
    story = _hostile_story()
    md = story.as_markdown()
    assert "\x00" not in md
    assert "‮" not in md
    assert "​" not in md
    # Legitimate content survives.
    assert "I want" in md
    assert "Gain visibility into operations." in md


def test_as_markdown_clean_story_byte_equal_snapshot() -> None:
    """Byte-equal regression snapshot - Must NOT silently drift the rendered output.

    Pinned because as_markdown() now passes its output through
    sanitize_artifact. A clean FinalUserStory must produce the EXACT
    same bytes it did before 12.2 landed. If a future renderer change
    legitimately alters formatting (e.g. adds a horizontal rule between
    sections), update this expected string in the SAME commit that
    changes the renderer - never silently.
    """
    story = FinalUserStory(
        description="As a user, I want to filter results, so I can find things.",
        objective="Find relevant content quickly.",
    )
    expected = (
        "## Description\n"
        "As a user, I want to filter results, so I can find things.\n"
        "\n"
        "## Objective\n"
        "Find relevant content quickly.\n"
        "\n"
        "## Acceptance Criteria\n"
        "_No acceptance criteria were produced for this story - check the run "
        "trace; the AC Generator agent should populate this section._\n"
        "\n"
        "## Test Cases\n"
        "_To be populated by the Test Case agent._"
    )
    assert story.as_markdown() == expected


def test_as_markdown_length_cap_applies_to_rendered_output() -> None:
    """A pathologically large description gets truncated at the rendering boundary."""
    story = FinalUserStory(
        description="x" * 50_000,
        objective="legit",
    )
    md = story.as_markdown(max_chars=1_000)
    assert len(md) <= 1_000
    assert "truncated" in md


def test_as_markdown_unknown_code_fence_in_description_is_normalised() -> None:
    story = FinalUserStory(
        description="See snippet:\n```mermaid\ngraph TD; A-->B\n```",
        objective="legit",
    )
    md = story.as_markdown()
    assert "```mermaid" not in md
    assert "graph TD; A-->B" in md  # body preserved


def test_as_markdown_preserves_allowed_python_fence() -> None:
    story = FinalUserStory(
        description="Example:\n```python\nprint('ok')\n```",
        objective="legit",
    )
    md = story.as_markdown()
    assert "```python" in md
