"""Tests for ``exporters._adf.markdown_to_adf`` (issue #223).

Pins the ADF shape produced for each piece of RTIA's known Markdown
output. Round-trips a full ``FinalUserStory.as_markdown()`` sample to
catch any drift between the artifact's renderer and the converter.
"""

from __future__ import annotations

import pytest

from agents.final_artifact import AcceptanceCriterion, FinalUserStory, TestCase
from exporters._adf import markdown_to_adf

# ---------- structural primitives ----------------------------------------


def test_h2_heading_becomes_adf_heading_level_2():
    doc = markdown_to_adf("## Description")
    assert doc["type"] == "doc"
    assert doc["version"] == 1
    [node] = doc["content"]
    assert node["type"] == "heading"
    assert node["attrs"]["level"] == 2
    assert node["content"] == [{"type": "text", "text": "Description"}]


def test_h4_heading_becomes_adf_heading_level_4():
    doc = markdown_to_adf("#### Toggle visible in header")
    node = doc["content"][0]
    assert node["type"] == "heading"
    assert node["attrs"]["level"] == 4


def test_plain_line_becomes_paragraph():
    doc = markdown_to_adf("As a user, I want a thing.")
    [node] = doc["content"]
    assert node["type"] == "paragraph"
    assert node["content"][0]["text"] == "As a user, I want a thing."


def test_bullet_list_becomes_bulletList():
    md = "- alpha\n- beta\n- gamma"
    doc = markdown_to_adf(md)
    [node] = doc["content"]
    assert node["type"] == "bulletList"
    assert len(node["content"]) == 3
    for item, expected in zip(node["content"], ["alpha", "beta", "gamma"], strict=True):
        assert item["type"] == "listItem"
        para = item["content"][0]
        assert para["type"] == "paragraph"
        assert para["content"][0]["text"] == expected


def test_numbered_steps_become_orderedList():
    md = "  1. First step.\n  2. Second step.\n  3. Third step."
    doc = markdown_to_adf(md)
    [node] = doc["content"]
    assert node["type"] == "orderedList"
    assert node["attrs"]["order"] == 1
    assert len(node["content"]) == 3
    assert node["content"][0]["content"][0]["content"][0]["text"] == "First step."


# ---------- inline marks -------------------------------------------------


def test_bold_span_becomes_strong_mark():
    doc = markdown_to_adf("**Given** I am logged in")
    para = doc["content"][0]
    runs = para["content"]
    assert runs[0] == {"type": "text", "text": "Given", "marks": [{"type": "strong"}]}
    assert runs[1] == {"type": "text", "text": " I am logged in"}


def test_italic_span_becomes_em_mark():
    doc = markdown_to_adf("Scenario name _(happy_path)_")
    para = doc["content"][0]
    runs = para["content"]
    assert any(r.get("marks") == [{"type": "em"}] for r in runs)
    em_text = next(r["text"] for r in runs if r.get("marks") == [{"type": "em"}])
    assert em_text == "(happy_path)"


def test_mixed_marks_in_one_paragraph():
    doc = markdown_to_adf("**Given** X; **When** Y; **Then** Z")
    runs = doc["content"][0]["content"]
    strong_texts = [r["text"] for r in runs if r.get("marks") == [{"type": "strong"}]]
    assert strong_texts == ["Given", "When", "Then"]


def test_unmatched_underscores_left_as_plain_text():
    """Underscores in the middle of words (e.g. variable names) must not
    be misread as italic delimiters."""
    doc = markdown_to_adf("Set DEBUG_MODE=true and run.")
    runs = doc["content"][0]["content"]
    assert len(runs) == 1
    assert runs[0]["text"] == "Set DEBUG_MODE=true and run."


# ---------- composition: full RTIA artifact -------------------------------


def test_full_artifact_round_trip_shape():
    """Convert a representative ``FinalUserStory.as_markdown()`` and
    pin the top-level node sequence the producer emits today."""
    art = FinalUserStory(
        description="As a tester, I want a dark-mode toggle.",
        objective="Reduce eye strain.",
        assumptions=["Browser supports localStorage."],
        acceptance_criteria=[
            AcceptanceCriterion(
                given="I am on the dashboard",
                when="I click the toggle",
                then="the theme switches",
            ),
        ],
        test_cases=[
            TestCase(
                scenario="Toggle visible in header",
                type="happy_path",
                steps=["Open dashboard.", "Look at header."],
                expected="Toggle is visible.",
            ),
        ],
        metadata={"review_summary": "[strong]"},
    )
    doc = markdown_to_adf(art.as_markdown())
    types = [n["type"] for n in doc["content"]]
    # Section headings appear and lists are real lists, not paragraphs.
    assert "heading" in types
    assert "bulletList" in types
    assert "orderedList" in types
    assert "paragraph" in types
    # No codeBlock - that was the legacy fallback shape.
    assert "codeBlock" not in types


def test_full_artifact_acs_render_as_bullets_with_strong_marks():
    art = FinalUserStory(
        description="d",
        objective="o",
        acceptance_criteria=[
            AcceptanceCriterion(given="G", when="W", then="T"),
        ],
    )
    doc = markdown_to_adf(art.as_markdown())
    bullet_list = next(n for n in doc["content"] if n["type"] == "bulletList")
    # The single AC becomes one listItem.
    [item] = bullet_list["content"]
    para = item["content"][0]
    strong_runs = [r for r in para["content"] if r.get("marks") == [{"type": "strong"}]]
    assert [r["text"] for r in strong_runs] == ["Given", "When", "Then"]


# ---------- contract / safety --------------------------------------------


def test_empty_input_raises():
    with pytest.raises(ValueError):
        markdown_to_adf("")
    with pytest.raises(ValueError):
        markdown_to_adf("   \n\n  \t  ")


def test_blank_lines_in_middle_are_skipped_not_emitted():
    """Blank separator lines between sections must not produce empty
    paragraphs - they should just disappear."""
    md = "## A\n\n\nfirst para\n\n\n## B"
    doc = markdown_to_adf(md)
    types = [n["type"] for n in doc["content"]]
    # heading, paragraph, heading - no stray paragraph nodes for blanks.
    assert types == ["heading", "paragraph", "heading"]


def test_h3_supported_even_though_artifact_doesnt_emit():
    """``###`` should still convert correctly in case the artifact's
    Markdown shape ever grows that level."""
    doc = markdown_to_adf("### Sub")
    assert doc["content"][0]["attrs"]["level"] == 3
