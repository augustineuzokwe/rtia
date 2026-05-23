"""Tests for the Gradio follow-up-export dispatch helper.

Regression cover for the bug where the UI's "Create follow-up issues"
button reported "No deferred stories — nothing to create." on a
``DONE_FANOUT`` thread even when four fan-out stories were visible in
the panel directly above it.

Root cause: the in-process Gradio handler called
``get_deferred_stories_and_context`` unconditionally. The API endpoint
``/pipeline/{thread_id}/export-deferred`` had the correct dispatch since
Phase 15.4, but the UI handler shipped without the equivalent branch.
The fix factored the decision into :func:`_select_followup_source` so
both surfaces share one helper; these tests pin its behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from api.models import ThreadStatus
from ui.gradio_app import _select_followup_source


def test_done_fanout_uses_fanout_source():
    runner = MagicMock()
    runner.get_fanout_stories_and_context.return_value = (["story"], "req")

    loaded, empty_msg = _select_followup_source(runner, "tid", ThreadStatus.DONE_FANOUT)

    runner.get_fanout_stories_and_context.assert_called_once_with("tid")
    runner.get_deferred_stories_and_context.assert_not_called()
    assert loaded == (["story"], "req")
    assert "fan-out" in empty_msg


def test_done_uses_deferred_source():
    runner = MagicMock()
    runner.get_deferred_stories_and_context.return_value = (["story"], "req")

    loaded, empty_msg = _select_followup_source(runner, "tid", ThreadStatus.DONE)

    runner.get_deferred_stories_and_context.assert_called_once_with("tid")
    runner.get_fanout_stories_and_context.assert_not_called()
    assert loaded == (["story"], "req")
    assert "deferred" in empty_msg


def test_paused_states_fall_through_to_deferred_source():
    """Anything that isn't DONE_FANOUT routes to the deferred path. The
    button is only visible in terminal states, but the helper shouldn't
    silently change behaviour for any non-fanout status."""
    for status in [ThreadStatus.PAUSED_PO, ThreadStatus.PAUSED_REVIEW, ThreadStatus.RUNNING]:
        runner = MagicMock()
        runner.get_deferred_stories_and_context.return_value = (None, None)

        _select_followup_source(runner, "tid", status)

        runner.get_deferred_stories_and_context.assert_called_once_with("tid")
        runner.get_fanout_stories_and_context.assert_not_called()


def test_loaded_none_propagates():
    """When the runner has no state for the thread (cleared SQLite, etc.)
    the helper returns ``None`` so the caller can render an explicit
    error rather than crash."""
    runner = MagicMock()
    runner.get_fanout_stories_and_context.return_value = None

    loaded, _ = _select_followup_source(runner, "tid", ThreadStatus.DONE_FANOUT)

    assert loaded is None


def test_dispatch_uses_status_not_thread_id():
    """Status alone drives the source choice. This pins the contract so a
    future refactor doesn't quietly reintroduce e.g. a path lookup off the
    thread_id instead of off the status enum."""
    runner = MagicMock()
    runner.get_fanout_stories_and_context.return_value = (["s"], "r")

    # Same thread_id, different statuses → different sources.
    _select_followup_source(runner, "shared-tid", ThreadStatus.DONE_FANOUT)
    runner.get_fanout_stories_and_context.assert_called_once()

    runner.reset_mock()
    runner.get_deferred_stories_and_context.return_value = (["s"], "r")
    _select_followup_source(runner, "shared-tid", ThreadStatus.DONE)
    runner.get_deferred_stories_and_context.assert_called_once()
    runner.get_fanout_stories_and_context.assert_not_called()
