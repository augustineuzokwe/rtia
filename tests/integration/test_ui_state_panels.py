"""Tests for ``_state_to_panels`` visibility mapping.

Regression cover for issue #175 - the deep-flow "Push to backlog" form
was visible on ``DONE_SPLIT`` threads, where it makes no sense (no deep
artifact exists). These tests pin the contract: ``deep_export_visible``
is True only on ``DONE``, hidden everywhere else; ``deferred_visible``
remains correctly populated for both terminal states.

The handler closure isn't directly testable (it's defined inside
``build_blocks``), but ``_state_to_panels`` is module-level and carries
the visibility decisions - testing it pins the contract where it lives.
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


def test_deep_export_hidden_on_done_split():
    """The core bug fix from #175 - split threads must NOT show the
    single-artifact 'Push to backlog' form."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={
            "split_stories": [
                {"title": "Story A", "summary": "a"},
                {"title": "Story B", "summary": "b"},
            ]
        },
    )

    panels = _state_to_panels(state)

    # Result panel still visible so the user can see the placeholder list.
    assert _visible(panels["result_visible"]) is True
    # But the deep-flow export form must be hidden.
    assert _visible(panels["deep_export_visible"]) is False


def test_split_result_text_points_at_correct_button():
    """The split result body should direct the user to 'Create
    follow-up issues', not the deep-flow 'Push to backlog' button.
    Misdirected copy was part of issue #175."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={"split_stories": [{"title": "X", "summary": "x"}]},
    )

    panels = _state_to_panels(state)

    md_update = panels["result_md"]
    md_value = md_update["value"] if isinstance(md_update, dict) else md_update.value
    assert "Create follow-up issues" in md_value
    assert "Push to backlog" not in md_value


def test_deep_export_hidden_in_non_terminal_states():
    """The form has no reason to show before the pipeline finishes -
    pin that so a refactor doesn't accidentally make it always-visible."""
    for status in [ThreadStatus.PAUSED_PO, ThreadStatus.PAUSED_REVIEW, ThreadStatus.RUNNING]:
        state = ThreadState(thread_id="tid", status=status, payload={})
        panels = _state_to_panels(state)
        assert _visible(panels["deep_export_visible"]) is False, (
            f"deep_export_visible should be False on {status.value}"
        )


def test_result_panel_visible_on_both_done_states():
    """Regression guard for #177 - ``result_panel`` must be visible on
    BOTH ``DONE`` and ``DONE_SPLIT`` because it now holds the shared
    export config fields (backend, target, dry_run) that both export
    handlers read from. If a future refactor accidentally hides
    ``result_panel`` on either terminal state, the split user loses
    the ability to toggle dry-run and the push silently no-ops."""
    for status, payload in [
        (ThreadStatus.DONE, {"rendered_artifact": "# x\n", "deferred_stories": []}),
        (
            ThreadStatus.DONE_SPLIT,
            {"split_stories": [{"title": "X", "summary": "x"}]},
        ),
    ]:
        state = ThreadState(thread_id="tid", status=status, payload=payload)
        panels = _state_to_panels(state)
        assert _visible(panels["result_visible"]) is True, (
            f"result_panel must be visible on {status.value} so the shared "
            "export config (backend/target/dry_run) is reachable"
        )


def test_state_to_panels_returns_stable_key_set():
    """Issue #186 §R1 - pin the dict keys ``_state_to_panels`` emits.

    The build-time ``assert`` inside ``build_blocks`` checks that
    ``_SPREAD_KEYS`` length matches the ``outputs`` list, but it can't
    catch a renamed key. This test fails fast if anyone adds, removes,
    or renames a key without updating both ends of the spread.
    """
    state = ThreadState(thread_id="tid", status=ThreadStatus.DONE, payload={})
    panels = _state_to_panels(state)

    expected = {
        "status_md",
        "thread_id_state",
        "po_visible",
        "po_questions",
        "po_split_rows",
        "po_split_originals",
        "po_answers_visible",
        "po_paused_payload",
        "review_visible",
        "review_preview",
        "result_visible",
        "result_md",
        "download_file",
        "backlog_visible",
        "deep_export_visible",
        "deferred_visible",
        "deferred_md",
        "deferred_checkboxes",
        "error_visible",
        "error_md",
        "run_btn_interactive",
    }
    assert set(panels) == expected, (
        f"_state_to_panels emitted unexpected keys: "
        f"missing={expected - set(panels)} extra={set(panels) - expected}"
    )


def test_unknown_status_renders_loud_error():
    """Issue #186 §R6 - a future graph change that adds a status must
    surface visibly, not silently render every panel hidden."""

    class _FakeStatus:
        value = "fake_new_status"

        def __repr__(self) -> str:
            return "FAKE_NEW_STATUS"

    class _FakeState:
        thread_id = "tid"
        status = _FakeStatus()
        payload: dict = {}

    panels = _state_to_panels(_FakeState())

    assert _visible(panels["error_visible"]) is True
    err_update = panels["error_md"]
    err_value = err_update["value"] if isinstance(err_update, dict) else err_update.value
    assert "Unknown pipeline status" in err_value
    assert "FAKE_NEW_STATUS" in err_value


def test_error_state_routes_to_error_panel_not_result_panel():
    """Issue #186 §6.1 - ERROR must not toggle ``result_panel`` because
    the Backlog target form was a structural child of it. Until #186,
    every pipeline failure surfaced the "Backlog target" config inputs
    with no actionable export button beneath them."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.ERROR,
        payload={"rendered_artifact": "boom: gemini 503"},
    )

    panels = _state_to_panels(state)

    assert _visible(panels["error_visible"]) is True
    assert _visible(panels["result_visible"]) is False
    assert _visible(panels["backlog_visible"]) is False
    assert _visible(panels["deep_export_visible"]) is False
    error_update = panels["error_md"]
    error_value = error_update["value"] if isinstance(error_update, dict) else error_update.value
    assert "boom: gemini 503" in error_value
    assert "Re-edit" in error_value


def test_error_state_with_no_message_still_renders_recovery_copy():
    state = ThreadState(thread_id="tid", status=ThreadStatus.ERROR, payload={})

    panels = _state_to_panels(state)

    assert _visible(panels["error_visible"]) is True
    error_update = panels["error_md"]
    error_value = error_update["value"] if isinstance(error_update, dict) else error_update.value
    assert "Re-edit" in error_value
    assert "Run pipeline" in error_value


def test_backlog_visible_on_both_terminal_success_states():
    """The shared Backlog target form drives BOTH the deep export
    button and the split follow-up button; it must be reachable on
    DONE and DONE_SPLIT (#186 §R3)."""
    for status, payload in [
        (ThreadStatus.DONE, {"rendered_artifact": "# x\n", "deferred_stories": []}),
        (
            ThreadStatus.DONE_SPLIT,
            {"split_stories": [{"title": "X", "summary": "x"}]},
        ),
    ]:
        state = ThreadState(thread_id="tid", status=status, payload=payload)
        panels = _state_to_panels(state)
        assert _visible(panels["backlog_visible"]) is True, (
            f"backlog_visible should be True on {status.value}"
        )


def test_backlog_hidden_in_non_terminal_states_and_error():
    for status in [
        ThreadStatus.RUNNING,
        ThreadStatus.PAUSED_PO,
        ThreadStatus.PAUSED_REVIEW,
        ThreadStatus.ERROR,
    ]:
        state = ThreadState(thread_id="tid", status=status, payload={})
        panels = _state_to_panels(state)
        assert _visible(panels["backlog_visible"]) is False, (
            f"backlog_visible should be False on {status.value}"
        )


def _interactive(update_obj) -> bool:
    if isinstance(update_obj, dict):
        return update_obj.get("interactive", True)
    return getattr(update_obj, "interactive", True)


def test_run_button_disabled_while_thread_is_active():
    """Issue #186 §6.4 - clicking Run mid-pipeline used to orphan the
    in-flight thread silently. The button is now disabled in every
    non-terminal state."""
    for status in [
        ThreadStatus.RUNNING,
        ThreadStatus.PAUSED_PO,
        ThreadStatus.PAUSED_REVIEW,
    ]:
        state = ThreadState(thread_id="tid", status=status, payload={})
        panels = _state_to_panels(state)
        assert _interactive(panels["run_btn_interactive"]) is False, (
            f"Run button must be disabled on {status.value}"
        )


def test_run_button_enabled_on_terminal_states():
    for status, payload in [
        (ThreadStatus.DONE, {"rendered_artifact": "# x\n", "deferred_stories": []}),
        (
            ThreadStatus.DONE_SPLIT,
            {"split_stories": [{"title": "X", "summary": "x"}]},
        ),
        (ThreadStatus.ERROR, {}),
    ]:
        state = ThreadState(thread_id="tid", status=status, payload=payload)
        panels = _state_to_panels(state)
        assert _interactive(panels["run_btn_interactive"]) is True, (
            f"Run button must stay enabled on {status.value} so the user can start a new run"
        )


def test_deferred_panel_still_works_on_done_split():
    """Regression guard for #170 - the follow-up panel must still be
    visible on DONE_SPLIT so the user can push the placeholders."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={
            "split_stories": [{"title": "X", "summary": "x"}],
        },
    )

    panels = _state_to_panels(state)

    assert _visible(panels["deferred_visible"]) is True


def test_split_row_checkboxes_have_no_visible_label():
    """Issue #213 - every pre-built split row checkbox must be built
    with ``show_label=False`` so Gradio doesn't fall back to its default
    literal "Checkbox" label. The neighbouring Textbox already labels
    the story; the checkbox is implicit "keep"."""
    from unittest.mock import MagicMock

    import gradio as gr
    from fastapi import FastAPI

    from ui.gradio_app import _MAX_SPLIT_ROWS, build_blocks

    app = FastAPI()
    app.state.runner = MagicMock()
    blocks = build_blocks(app)

    def _walk(comp):
        if isinstance(comp, gr.Checkbox):
            yield comp
        if hasattr(comp, "children"):
            for child in comp.children:
                yield from _walk(child)

    checkboxes = list(_walk(blocks))
    # First N checkboxes are the split row checkboxes (declaration
    # order); the "Dry run" checkbox sits after them.
    split_chks = checkboxes[:_MAX_SPLIT_ROWS]
    assert len(split_chks) == _MAX_SPLIT_ROWS, (
        f"expected {_MAX_SPLIT_ROWS} split checkboxes, found {len(split_chks)}"
    )
    for i, chk in enumerate(split_chks):
        assert chk.show_label is False, (
            f"split checkbox row {i} must have show_label=False "
            f"(got show_label={chk.show_label!r}, label={chk.label!r})"
        )


def _checkboxes_visible(update_obj) -> bool:
    if isinstance(update_obj, dict):
        return update_obj.get("visible", False)
    return getattr(update_obj, "visible", False)


def test_deferred_checkboxgroup_hidden_on_done_split():
    """Issue #214 - on DONE_SPLIT the PO already picked/renamed/dropped
    at the editable PO checkpoint (#207). Re-surfacing a second
    CheckboxGroup with the same titles is redundant and looks like the
    upstream selection didn't take. The panel + button stay visible so
    the PO can trigger the push; the redundant title list is suppressed."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={
            "split_stories": [
                {"title": "Story A", "summary": "a"},
                {"title": "Story B", "summary": "b"},
            ]
        },
    )
    panels = _state_to_panels(state)
    # Panel (with the export-deferred button inside) must stay visible.
    assert _visible(panels["deferred_visible"]) is True
    # CheckboxGroup must NOT be visible - that's the redundancy we removed.
    assert _checkboxes_visible(panels["deferred_checkboxes"]) is False
    # And the dangling label that used to head the list is dropped.
    md_update = panels["deferred_md"]
    md_value = md_update["value"] if isinstance(md_update, dict) else md_update.value
    assert md_value == ""


def test_deferred_checkboxgroup_still_shown_on_done_with_deferred_stories():
    """Issue #214 - the DONE (deep flow) branch is unchanged. There, the
    PO picked ONE story at the PO checkpoint and the rest were *inferred*
    deferred; the opt-out CheckboxGroup is the only place the PO sees
    them, so it must stay visible."""
    state = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE,
        payload={
            "rendered_artifact": "# Story\n",
            "deferred_stories": [
                {"title": "Other story 1", "summary": "x"},
                {"title": "Other story 2", "summary": "y"},
            ],
        },
    )
    panels = _state_to_panels(state)
    assert _visible(panels["deferred_visible"]) is True
    assert _checkboxes_visible(panels["deferred_checkboxes"]) is True
