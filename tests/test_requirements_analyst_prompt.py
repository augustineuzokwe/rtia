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


def test_prompt_includes_worked_example_with_empty_ambiguities():
    """A concrete input→output example is the strongest lever for LLM compliance.

    The example must show an empty ambiguities list as a *valid output*, so the
    model learns that zero ambiguities is normal — not a failure mode. Without
    this, the model defaults to padding the list to look thorough.
    """
    lower = SYSTEM_PROMPT.lower()
    assert "worked example" in lower
    assert '"ambiguities": []' in SYSTEM_PROMPT, (
        "Worked example must show an empty ambiguities list as a valid output."
    )
    # The example must include both the input and the correct output sections.
    assert "requirement (input)" in lower
    assert "correct output" in lower


def test_prompt_defines_multi_feature_rule():
    """The Analyst must detect multi-feature requirements and ask PO to pick one."""
    lower = SYSTEM_PROMPT.lower()
    assert "multi-feature" in lower
    assert "implied_stories" in SYSTEM_PROMPT
    # The rule MUST require pairing with a critical ambiguity (so the PO checkpoint pauses).
    assert "critical ambiguity" in lower or "critical' ambiguity" in lower
    # Single-story default must be explicit so model doesn't always populate the list.
    assert "empty list" in lower
    assert "single-story" in lower


def test_prompt_includes_multi_feature_worked_example():
    """A worked example for the multi-feature case is the strongest lever.

    Without it, the model treats the rule as advisory and rarely populates
    implied_stories — the very behavior we're trying to fix on sample-03.
    """
    lower = SYSTEM_PROMPT.lower()
    assert "multi-feature case" in lower or "multi-feature rule" in lower
    # The example must show implied_stories populated and a critical ambiguity.
    assert '"implied_stories":' in SYSTEM_PROMPT
    # And it must show concrete story splits to anchor the pattern.
    assert "filter" in lower and "csv" in lower


def test_prompt_explains_why_anti_examples_are_rejected_in_the_worked_example():
    """The worked example must explicitly reject specific would-be ambiguities.

    Showing "you might be tempted to flag X but don't, because Y" teaches the
    classification boundary in a way prose rules alone don't. Without this,
    the example is just a happy-path; the model still hits the same trap on
    edge cases.
    """
    lower = SYSTEM_PROMPT.lower()
    assert "tempted to flag" in lower or "you might" in lower
    # At least two distinct rejection categories should be reasoned through.
    rejection_categories = [
        "ac concern",
        "ux/implementation",
        "edge-case",
        "new story",
        "don't invent",
    ]
    matched = [c for c in rejection_categories if c in lower]
    assert len(matched) >= 2, (
        f"Worked example should explicitly reason through at least 2 of "
        f"{rejection_categories}; got {matched}."
    )
