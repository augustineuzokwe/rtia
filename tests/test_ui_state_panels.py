"""Tests for ``_state_to_panels`` visibility mapping.

Regression cover for issue #175 — the deep-flow "Push to backlog" form
was visible on ``DONE_FANOUT`` threads, where it makes no sense (no deep
artifact exists). These tests pin the contract: ``deep_export_visible``
is True only on ``DONE``, hidden everywhere else; ``deferred_visible``
remains correctly populated for both terminal states.

The handler closure isn't directly testable (it's defined inside
``build_blocks``), but ``_state_to_panels`` is module-level and carries
the visibility decisions — testing it pins the contract where it lives.
"""

from __future__ import annotations

from api.models import ThreadState, ThreadStatus
from ui.gradio_app import _state_to_panels


def _visible(update_obj) -> bool:
    """Read the ``visible`` field off a gradio update dict-or-object."""
    # gr.update returns a dict in tests where Gradio isn't fully running.
    if isinstance(update_obj, dict):
        return update_obj.get("visible", False)
    return getattr(update_obj, "visible", False)


def test_deep_export_visible_on_done():
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE,
        payload={"rendered_artifact": "# Story\n", "deferred_stories": []},
    )

    panels = _state_to_panels(state)

    assert _visible(panels["result_visible"]) is True
    assert _visible(panels["deep_export_visible"]) is True


def test_deep_export_hidden_on_done_fanout():
    """The core bug fix from #175 — fan-out threads must NOT show the
    single-artifact 'Push to backlog' form."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_FANOUT,
        payload={
            "fan_out_stories": [
                {"title": "Story A", "summary": "a"},
                {"title": "Story B", "summary": "b"},
            ]
        },
    )

    panels = _state_to_panels(state)

    # Result panel still visible so the user can see the stub list.
    assert _visible(panels["result_visible"]) is True
    # But the deep-flow export form must be hidden.
    assert _visible(panels["deep_export_visible"]) is False


def test_fanout_result_text_points_at_correct_button():
    """The fan-out result body should direct the user to 'Create
    follow-up issues', not the deep-flow 'Push to backlog' button.
    Misdirected copy was part of issue #175."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_FANOUT,
        payload={"fan_out_stories": [{"title": "X", "summary": "x"}]},
    )

    panels = _state_to_panels(state)

    md_update = panels["result_md"]
    md_value = md_update["value"] if isinstance(md_update, dict) else md_update.value
    assert "Create follow-up issues" in md_value
    assert "Push to backlog" not in md_value


def test_deep_export_hidden_in_non_terminal_states():
    """The form has no reason to show before the pipeline finishes —
    pin that so a refactor doesn't accidentally make it always-visible."""
    for status in [ThreadStatus.PAUSED_PO, ThreadStatus.PAUSED_REVIEW, ThreadStatus.RUNNING]:
        state = ThreadState(thread_id="tid", status=status, payload={})
        panels = _state_to_panels(state)
        assert _visible(panels["deep_export_visible"]) is False, (
            f"deep_export_visible should be False on {status.value}"
        )


def test_deferred_panel_still_works_on_done_fanout():
    """Regression guard for #170 — the follow-up panel must still be
    visible on DONE_FANOUT so the user can push the stubs."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_FANOUT,
        payload={
            "fan_out_stories": [{"title": "X", "summary": "x"}],
        },
    )

    panels = _state_to_panels(state)

    assert _visible(panels["deferred_visible"]) is True
