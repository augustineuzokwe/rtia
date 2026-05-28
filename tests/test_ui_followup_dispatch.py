"""Tests for the shared follow-up-export dispatch helpers.

Regression cover for the bug where the UI's "Create follow-up issues"
button reported "No deferred stories - nothing to create." on a
``DONE_SPLIT`` thread even when four split stories were visible in
the panel directly above it.

Root cause: the in-process Gradio handler called
``get_deferred_stories_and_context`` unconditionally. The API endpoint
``/pipeline/{thread_id}/export-deferred`` had the correct dispatch since
but the UI handler shipped without the equivalent branch.

The dispatch lived in two places (a UI helper + inline in the API
endpoint) until issue #194 / R2 lifted it into :mod:`api._shared` so
both surfaces share one implementation. These tests pin the shared
helpers' behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from api._shared import (
    build_followup_markdown,
    followup_empty_message,
    select_followup_source,
)
from api.models import ThreadStatus


def test_done_split_uses_split_source():
    runner = MagicMock()
    runner.get_split_stories_and_context.return_value = (["story"], "req")

    loaded = select_followup_source(runner, "tid", ThreadStatus.DONE_SPLIT)

    runner.get_split_stories_and_context.assert_called_once_with("tid")
    runner.get_deferred_stories_and_context.assert_not_called()
    assert loaded == (["story"], "req")


def test_done_uses_deferred_source():
    runner = MagicMock()
    runner.get_deferred_stories_and_context.return_value = (["story"], "req")

    loaded = select_followup_source(runner, "tid", ThreadStatus.DONE)

    runner.get_deferred_stories_and_context.assert_called_once_with("tid")
    runner.get_split_stories_and_context.assert_not_called()
    assert loaded == (["story"], "req")


def test_paused_states_fall_through_to_deferred_source():
    """Anything that isn't DONE_SPLIT routes to the deferred path. The
    button is only visible in terminal states, but the helper shouldn't
    silently change behaviour for any non-split status."""
    for status in [ThreadStatus.PAUSED_PO, ThreadStatus.PAUSED_REVIEW, ThreadStatus.RUNNING]:
        runner = MagicMock()
        runner.get_deferred_stories_and_context.return_value = (None, None)

        select_followup_source(runner, "tid", status)

        runner.get_deferred_stories_and_context.assert_called_once_with("tid")
        runner.get_split_stories_and_context.assert_not_called()


def test_loaded_none_propagates():
    """When the runner has no state for the thread (cleared SQLite, etc.)
    the helper returns ``None`` so the caller can render an explicit
    error rather than crash."""
    runner = MagicMock()
    runner.get_split_stories_and_context.return_value = None

    loaded = select_followup_source(runner, "tid", ThreadStatus.DONE_SPLIT)

    assert loaded is None


def test_dispatch_uses_status_not_thread_id():
    """Status alone drives the source choice. This pins the contract so a
    future refactor doesn't quietly reintroduce e.g. a path lookup off the
    thread_id instead of off the status enum."""
    runner = MagicMock()
    runner.get_split_stories_and_context.return_value = (["s"], "r")

    # Same thread_id, different statuses → different sources.
    select_followup_source(runner, "shared-tid", ThreadStatus.DONE_SPLIT)
    runner.get_split_stories_and_context.assert_called_once()

    runner.reset_mock()
    runner.get_deferred_stories_and_context.return_value = (["s"], "r")
    select_followup_source(runner, "shared-tid", ThreadStatus.DONE)
    runner.get_deferred_stories_and_context.assert_called_once()
    runner.get_split_stories_and_context.assert_not_called()


def test_followup_empty_message_pivots_on_split():
    """The status-matched empty-state copy is the UI's only need for the
    legacy ``(loaded, empty_msg)`` tuple - extracted into its own helper
    so both wordings live next to the dispatch they're paired with."""
    assert "split" in followup_empty_message(ThreadStatus.DONE_SPLIT)
    assert "deferred" in followup_empty_message(ThreadStatus.DONE)
    # Non-terminal statuses follow the "deferred" wording - the message
    # is rarely surfaced from those states, but the contract is
    # "anything not DONE_SPLIT" matches the dispatch.
    assert "deferred" in followup_empty_message(ThreadStatus.PAUSED_PO)


def test_build_followup_markdown_basic_shape():
    """Sanity check the body template - title heading, summary, provenance
    block, and excerpt section. Pins the output so both surfaces produce
    byte-identical issue bodies."""
    md = build_followup_markdown(
        "Story A",
        "As a user…",
        requirement_excerpt="The original requirement text.",
    )
    assert md.startswith("## Story A")
    assert "As a user…" in md
    assert "## Provenance" in md
    assert "## Originating requirement (excerpt)" in md
    assert "The original requirement text." in md


def test_build_followup_markdown_truncates_long_excerpt():
    """Excerpts over the cap are truncated with an ellipsis so the issue
    body stays readable. The cap default (800) matches the legacy
    pre-extraction value - must not silently change."""
    excerpt = "x" * 1200
    md = build_followup_markdown("T", "S", requirement_excerpt=excerpt)
    assert "…" in md
    # Verify the truncated excerpt block doesn't exceed cap + ellipsis.
    excerpt_block = md.split("## Originating requirement (excerpt)\n\n", 1)[1]
    assert len(excerpt_block.rstrip()) <= 801  # 800 chars + "…"


def test_build_followup_markdown_omits_excerpt_section_when_empty():
    md = build_followup_markdown("T", "S", requirement_excerpt="")
    assert "## Originating requirement (excerpt)" not in md
    assert "## Provenance" in md
