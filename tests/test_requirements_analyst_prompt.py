"""Contract tests for the Requirements Analyst prompt.

These tests guard the prompt's *shape* — specific rules and examples that
downstream behavior depends on. They don't validate Claude's compliance
(that needs a live eval harness); they prevent silent removal of rules
during future edits.

If a rule here trips legitimately because the prompt was rewritten with
better wording, update the assertion to match the new wording. Don't
delete the assertion without also deleting the rule.
"""

from __future__ import annotations

from prompts.requirements_analyst_prompts import SYSTEM_PROMPT


def test_prompt_defines_story_shape_scope_rule():
    """The Analyst must be told to flag only story-shape questions."""
    assert "story-shape" in SYSTEM_PROMPT.lower() or "story's shape" in SYSTEM_PROMPT.lower()
    assert "role" in SYSTEM_PROMPT and "action" in SYSTEM_PROMPT


def test_prompt_excludes_implementation_and_ux_details():
    """The Analyst must be told NOT to flag implementation/UX/edge-case details."""
    lower = SYSTEM_PROMPT.lower()
    assert "implementation" in lower
    assert "ux" in lower or "edge-case" in lower or "edge case" in lower


def test_prompt_lists_concrete_anti_examples():
    """The Analyst prompt should include concrete examples of what NOT to flag.

    Cheap anti-regression: catches accidental deletion of the example block
    that turned out to matter for output quality.
    """
    lower = SYSTEM_PROMPT.lower()
    # At least a couple of the recurring over-flagged topics from sample-01.
    anti_example_topics = ["refresh", "timestamp", "error", "redirect", "empty state"]
    matched = [t for t in anti_example_topics if t in lower]
    assert len(matched) >= 3, (
        f"Expected the prompt to anti-example at least 3 of {anti_example_topics}, got {matched}."
    )


def test_prompt_prefers_fewer_higher_signal_ambiguities():
    """The Analyst must be steered toward fewer, higher-signal ambiguities."""
    lower = SYSTEM_PROMPT.lower()
    assert "fewer" in lower and "higher-signal" in lower
