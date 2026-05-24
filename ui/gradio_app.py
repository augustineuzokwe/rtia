"""Gradio Blocks UI for RTIA — paste-or-upload, run, review, export.

In-process: calls the shared ``PipelineRunner`` on the FastAPI app's state
directly instead of going over HTTP. That keeps the latency low and means
the UI never has to know its own auth token (the mount handles auth at
the request layer; the in-process call sits inside the same auth scope).

Pause flow is encoded as separate visible panels:

* **Input panel** — paste text or upload PDF/Markdown. "Run" starts the
  pipeline.
* **PO panel** — visible only when the pipeline pauses at the PO
  checkpoint. Lists each critical question; PO supplies an answer.
* **Review panel** — visible only when the pipeline pauses at the story
  review checkpoint. Shows the rendered preview; "Accept" or
  "Override" submits the resume.
* **Result panel** — visible when the pipeline is done. Shows the
  rendered artifact + a Download Markdown button.
"""

from __future__ import annotations

from typing import Any

import gradio as gr
from fastapi import FastAPI

from api._shared import (
    build_followup_markdown,
    followup_empty_message,
    select_followup_source,
)
from api.models import ThreadStatus
from api.parsers import (
    FileTooLargeError,
    ParseError,
    ScannedPdfError,
    extract_markdown,
    extract_pdf,
)
from api.runner import PipelineRunner
from exporters.base import (
    ExportConfigError,
    ExporterTransportError,
    ExportTarget,
    make_exporter,
)


def _runner(app: FastAPI) -> PipelineRunner:
    return app.state.runner


# Issue #207 — editable fan-out title rows. Gradio Blocks must declare
# every component at build time, so we pre-build a fixed maximum number
# of (Checkbox + Textbox) row pairs and toggle their visibility per
# render. Ten is a generous ceiling — the Analyst's multi-story output
# in practice tops out around 4–5 implied stories.
_MAX_FANOUT_ROWS = 10


def _build_export_target(backend: str, target: str, extra: str) -> ExportTarget:
    """Map the three Backlog-target Textbox values onto an ``ExportTarget``.

    Issue #215 — extracted from ``on_export`` / ``on_export_deferred``
    where the same logic was duplicated. Centralising lets a unit test
    pin the "extra → ``github_project_number``" hop that the round-1
    walk-through left unverified.

    Conventions (matched to the Textbox labels):

    - ``target`` is the Jira project key (e.g. ``"RTIA"``) OR the
      GitHub repo in ``owner/name`` form. Empty → ``None``.
    - ``extra`` is the Jira parent-epic key (e.g. ``"RTIA-1"``) OR the
      GitHub project number as a digit string (e.g. ``"5"``). Empty
      or non-digit-for-GitHub → ``None`` (no parent / no project add).
    """
    target = (target or "").strip()
    extra = (extra or "").strip()
    if backend == "jira":
        return ExportTarget(
            backend="jira",
            jira_project_key=target or None,
            jira_parent_key=extra or None,
        )
    return ExportTarget(
        backend="github",
        github_repo=target or None,
        github_project_number=int(extra) if extra.isdigit() else None,
    )


def _hidden_fanout_rows() -> list[tuple[Any, Any, Any]]:
    """Build a list of N hidden ``(row, checkbox, textbox)`` updates.

    Default state for the editable fan-out row slots — every slot
    invisible, unchecked, empty. Used in non-PAUSED_PO branches and on
    the empty-input / running-frame paths.
    """
    return [
        (
            gr.update(visible=False),
            gr.update(value=False),
            gr.update(value=""),
        )
        for _ in range(_MAX_FANOUT_ROWS)
    ]


def _state_to_panels(state) -> dict[str, Any]:
    """Map a ThreadState onto Gradio update objects for each panel.

    Returned as a dict the caller spreads into the relevant outputs;
    keeping the mapping in one place keeps the event handlers below
    readable.

    Panel-visibility flags (one per gated group):

    - ``po_visible`` — PO checkpoint inputs
    - ``review_visible`` — Story review checkpoint
    - ``result_visible`` — Rendered artifact / fan-out stub list
    - ``backlog_visible`` — Shared "Backlog target" config form
      (backend / target / extras / dry-run). Visible on both terminal
      success states; hidden on ERROR so the user can't pre-fill a form
      that connects to nothing actionable. See issue #186 §6.1.
    - ``deep_export_visible`` — "Push to backlog" button (deep flow only)
    - ``deferred_visible`` — Deferred/fan-out follow-up panel
    - ``error_visible`` — Dedicated error panel (sibling of
      ``result_panel``); replaces the prior pattern of overloading
      ``result_panel`` with the error message.
    - ``run_btn_interactive`` — ``False`` while a thread is active so
      the Run button can't silently orphan an in-flight run (issue #186
      §6.4).
    """
    status_label = f"Status: **{state.status.value}**"
    base: dict[str, Any] = {
        "status_md": status_label,
        "thread_id_state": state.thread_id,
        "po_visible": gr.update(visible=False),
        "review_visible": gr.update(visible=False),
        "result_visible": gr.update(visible=False),
        "backlog_visible": gr.update(visible=False),
        # Deep-flow export button: visible only on DONE. See ADR-0010
        # and issue #175 for context.
        "deep_export_visible": gr.update(visible=False),
        "error_visible": gr.update(visible=False),
        "error_md": gr.update(value=""),
        "run_btn_interactive": gr.update(interactive=True),
        "po_questions": gr.update(value=""),
        "po_answers_visible": gr.update(visible=True),
        # Issue #207 — list of N fan-out row updates, one tuple per
        # pre-built ``(Row, Checkbox, Textbox)`` slot. Tuple shape:
        # ``(row_update, checkbox_update, textbox_update)``. Hidden by
        # default; populated only on PAUSED_PO + fan_out mode below.
        "po_fanout_rows": _hidden_fanout_rows(),
        "po_fanout_originals": [],
        "po_paused_payload": {},
        "review_preview": gr.update(value=""),
        "result_md": gr.update(value=""),
        "download_file": gr.update(value=None, visible=False),
        "deferred_visible": gr.update(visible=False),
        "deferred_md": gr.update(value=""),
        "deferred_checkboxes": gr.update(choices=[], value=[], visible=False),
    }

    # Lock the Run button whenever a thread is active so clicking it
    # can't orphan in-flight work (#186 §6.4). Terminal and error states
    # leave the button enabled — the user is free to start a fresh run.
    if state.status in (
        ThreadStatus.RUNNING,
        ThreadStatus.PAUSED_PO,
        ThreadStatus.PAUSED_REVIEW,
    ):
        base["run_btn_interactive"] = gr.update(interactive=False)

    if state.status == ThreadStatus.PAUSED_PO:
        mode = state.payload.get("mode", "deep")
        base["po_visible"] = gr.update(visible=True)
        base["po_paused_payload"] = dict(state.payload)
        questions = state.payload.get("critical_ambiguities", [])
        if mode == "fan_out":
            # Phase 15.4 — CheckboxGroup for implied stories + text input
            # for any remaining non-story critical questions.
            stories = state.payload.get("implied_stories", [])
            preview_lines = [
                "### Multi-story requirement detected",
                "",
                f"The Analyst identified **{len(stories)} implied stories**. ",
                "RTIA will fan these out as lightweight backlog issues — ",
                "no deep artifact this session. Uncheck any you don't want; ",
                "submit to create them. Re-run RTIA on any title later for ",
                "the full Description / Objective / ACs / Test Cases.",
                "",
            ]
            for s in stories:
                preview_lines.append(f"- **{s['title']}** — {s['summary']}")
            if questions:
                preview_lines.extend(
                    [
                        "",
                        "**Other critical questions** (one answer per line, in order):",
                        "",
                    ]
                )
                preview_lines.extend(f"{i + 1}. {q}" for i, q in enumerate(questions))
            base["po_questions"] = gr.update(value="\n".join(preview_lines))
            # Issue #207 — one editable row per implied story (up to the
            # build-time max). Each row carries its own checkbox + title
            # Textbox; un-checked rows are dropped at Submit, edited
            # titles flow into ``selected_stories`` on the resume body.
            rows: list[tuple[Any, Any, Any]] = []
            originals: list[str] = []
            for i in range(_MAX_FANOUT_ROWS):
                if i < len(stories):
                    title = stories[i].get("title", "") or ""
                    rows.append(
                        (
                            gr.update(visible=True),
                            gr.update(value=True),
                            gr.update(value=title, label=f"Story {i + 1}"),
                        )
                    )
                    originals.append(title)
                else:
                    rows.append(
                        (
                            gr.update(visible=False),
                            gr.update(value=False),
                            gr.update(value=""),
                        )
                    )
            base["po_fanout_rows"] = rows
            base["po_fanout_originals"] = originals
            # Hide the free-text answers box when there are no non-story Qs.
            base["po_answers_visible"] = gr.update(visible=bool(questions))
        else:
            formatted = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
            base["po_questions"] = gr.update(
                value=(
                    f"### {len(questions)} critical question(s) — "
                    "answer each on a separate line, in order:\n\n" + formatted
                )
            )
    elif state.status == ThreadStatus.PAUSED_REVIEW:
        base["review_visible"] = gr.update(visible=True)
        base["review_preview"] = gr.update(value=state.payload.get("rendered_artifact", ""))
    elif state.status == ThreadStatus.DONE:
        rendered = state.payload.get("rendered_artifact", "")
        base["result_visible"] = gr.update(visible=True)
        # Shared backlog-target form is visible on both success terminals
        # (used by deep export AND fan-out follow-up exports).
        base["backlog_visible"] = gr.update(visible=True)
        # Deep flow produced an artifact — the single-artifact "Push to
        # backlog" button is the right control here.
        base["deep_export_visible"] = gr.update(visible=True)
        base["result_md"] = gr.update(value=rendered)
        # write a temp .md the user can click to download
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(rendered)
            base["download_file"] = gr.update(value=fh.name, visible=True)
        # Phase 15.3 — surface deferred implied stories so the PO can
        # batch-create follow-up issues from the same panel.
        deferred = state.payload.get("deferred_stories") or []
        base["deferred_visible"] = gr.update(visible=bool(deferred))
        if deferred:
            md_lines = ["### Deferred stories", ""]
            for s in deferred:
                md_lines.append(f"- **{s['title']}** — {s['summary']}")
            base["deferred_md"] = gr.update(value="\n".join(md_lines))
            base["deferred_checkboxes"] = gr.update(
                choices=[s["title"] for s in deferred],
                value=[s["title"] for s in deferred],
                visible=True,
            )
        else:
            base["deferred_md"] = gr.update(value="")
            base["deferred_checkboxes"] = gr.update(choices=[], value=[], visible=False)
    elif state.status == ThreadStatus.DONE_FANOUT:
        # Phase 15.4 — fan-out terminal state. No deep artifact; render
        # the lightweight stub list and reuse the deferred-stories
        # CheckboxGroup + Push-to-backlog flow.
        stubs = state.payload.get("fan_out_stories") or []
        base["result_visible"] = gr.update(visible=True)
        # Shared backlog-target form is visible — the fan-out export
        # below reads from the same fields.
        base["backlog_visible"] = gr.update(visible=True)
        # No deep artifact in fan-out mode — keep the single-artifact
        # "Push to backlog" button hidden so the PO uses the correct
        # "Create follow-up issues" control below.
        base["deep_export_visible"] = gr.update(visible=False)
        md_lines = [
            "### Fan-out result",
            "",
            f"RTIA produced **{len(stubs)} lightweight backlog stubs**.",
            "Click *Create follow-up issues* below to create them in Jira / GitHub.",
            "",
            "_Re-run RTIA on any individual title to deep-dive that story._",
            "",
        ]
        for s in stubs:
            md_lines.append(f"- **{s['title']}** — {s['summary']}")
        base["result_md"] = gr.update(value="\n".join(md_lines))
        # Issue #214 — on DONE_FANOUT the PO already made the keep/drop
        # decision seconds earlier at the editable fan-out PO checkpoint
        # (#207). Surfacing a second "Push these to the backlog"
        # CheckboxGroup with the same titles looks like the upstream
        # selection didn't take, and is the round-2 walk-through Wart 2.
        # Keep the panel + button visible so the PO can trigger the push,
        # but suppress the redundant title list. ``on_export_deferred``
        # treats an empty selection as "all stubs" — this preserves the
        # one-click semantics.
        base["deferred_visible"] = gr.update(visible=bool(stubs))
        if stubs:
            base["deferred_md"] = gr.update(value="")
            base["deferred_checkboxes"] = gr.update(
                choices=[s["title"] for s in stubs],
                value=[],
                visible=False,
            )
    elif state.status == ThreadStatus.ERROR:
        # #186 §6.1 — ERROR no longer rides on ``result_panel``. Routing
        # to a dedicated ``error_panel`` keeps the "Backlog target"
        # form (a child of ``result_panel``) hidden, so the user can't
        # pre-fill inputs that connect to nothing actionable.
        rendered = state.payload.get("rendered_artifact", "")
        base["error_visible"] = gr.update(visible=True)
        body = (
            f"### Pipeline error\n\n{rendered}\n\n"
            "_Re-edit the requirement above and click **Run pipeline** "
            "to try again._"
            if rendered
            else (
                "### Pipeline error\n\n"
                "The pipeline failed without a recoverable message. "
                "Re-edit the requirement above and click **Run pipeline** "
                "to try again."
            )
        )
        base["error_md"] = gr.update(value=body)
    elif state.status == ThreadStatus.RUNNING:
        # Transient state — no panels open. Kept as an explicit branch
        # so future affordances (spinner, partial preview) have a home
        # and don't have to share a fallthrough with the idle path.
        pass
    else:
        # #186 §R6 — unknown status. Failing loud here means a future
        # graph change that adds a status surfaces visibly instead of
        # silently rendering every panel hidden.
        base["error_visible"] = gr.update(visible=True)
        base["error_md"] = gr.update(
            value=(
                f"### Unknown pipeline status: `{state.status!r}`\n\n"
                "This is a bug — please report it with the status value "
                "above and the input that produced it."
            )
        )
    return base


def build_blocks(app: FastAPI) -> gr.Blocks:
    """Construct the Gradio Blocks; bind handlers that hit the runner."""

    with gr.Blocks(title="RTIA") as blocks:
        gr.Markdown(
            "# RTIA — Requirements & Test Intelligence Assistant\n"
            "Paste a requirement or upload a PDF / Markdown file. The "
            "pipeline pauses for PO input on critical ambiguities and "
            "again for story review before emitting the final artifact."
        )

        thread_id_state = gr.State(value="")
        status_md = gr.Markdown("Status: **idle**")

        # Compact intake layout. Before: 12-line textbox + two stacked
        # full-size gr.File drop zones consumed ~900px before-fold; users
        # had to scroll just to see the Run button. Now: a tighter
        # textbox (paragraphs still grow it dynamically) and the two
        # uploads side-by-side in a Row, with reduced drop-zone height.
        # Same handlers, same widgets — just denser packing.
        with gr.Row(), gr.Column(scale=2):
            req_text = gr.Textbox(
                label="Requirement text", lines=6, placeholder="Paste raw requirements here…"
            )
            with gr.Row():
                upload_pdf_input = gr.File(label="…or upload a PDF", file_types=[".pdf"], height=80)
                upload_md_input = gr.File(
                    label="…or upload Markdown", file_types=[".md"], height=80
                )
            run_btn = gr.Button("Run pipeline", variant="primary")
            upload_status = gr.Markdown("")

        # PO checkpoint panel.
        with gr.Group(visible=False) as po_panel:
            po_questions = gr.Markdown("")
            # Issue #207 — N editable (Checkbox + Textbox) rows for the
            # fan-out case. Replaces the prior single ``gr.CheckboxGroup``
            # which only supported drop / keep but not rename. Rows are
            # built at definition time and visibility-toggled per render;
            # this is the standard Gradio Blocks pattern for variable-N
            # widgets. Hidden in deep mode and on non-PAUSED_PO states.
            po_fanout_rows_components: list[tuple[gr.Row, gr.Checkbox, gr.Textbox]] = []
            for i in range(_MAX_FANOUT_ROWS):
                with gr.Row(visible=False) as _fanout_row:
                    # Issue #213 — pass ``show_label=False`` so Gradio
                    # doesn't fall back to its default literal "Checkbox"
                    # label (``label=""`` alone doesn't suppress the
                    # fallback). The row's adjacent Textbox already
                    # labels the story; the checkbox is implicit "keep".
                    _fanout_chk = gr.Checkbox(value=False, show_label=False, scale=0)
                    _fanout_txt = gr.Textbox(value="", label=f"Story {i + 1}", interactive=True)
                po_fanout_rows_components.append((_fanout_row, _fanout_chk, _fanout_txt))
            # Snapshot of the original (pre-edit) titles from the paused
            # payload, kept aligned with the visible rows. Submit handler
            # uses this to map an edited title back to the matching
            # Analyst-provided summary even after the PO renamed the row.
            po_fanout_originals_state = gr.State(value=[])
            po_answers = gr.Textbox(
                label="Answers (one per line, in order)",
                lines=4,
                placeholder="Answer 1\nAnswer 2\n…",
            )
            # Holds the paused payload between renders so on_po_submit
            # knows which mode to build the resume body for.
            po_paused_payload_state = gr.State(value={})
            po_submit = gr.Button("Submit", variant="primary")

        # Story review panel.
        #
        # Layout: review preview → primary "Accept as-is" button (takes
        # the Story Writer's output unchanged) → override-edit section
        # (fields + "Override" button right beneath them, so it's clear
        # which control acts on which inputs). The prior layout placed
        # both buttons in a single row ABOVE the textboxes, which made
        # the textboxes look orphaned and the Override button look like
        # it acted on the rendered preview rather than the edit fields.
        with gr.Group(visible=False) as review_panel:
            review_preview = gr.Markdown("")
            review_accept = gr.Button("Accept as-is", variant="primary")
            gr.Markdown("---\n#### …or edit and override")
            override_description = gr.Textbox(
                label="New description (leave blank to keep)", lines=2, visible=True
            )
            override_objective = gr.Textbox(
                label="New objective (leave blank to keep)", lines=2, visible=True
            )
            review_override = gr.Button("Override")

        # Result panel — artifact preview + download only.
        with gr.Group(visible=False) as result_panel:
            result_md = gr.Markdown("")
            download_file = gr.File(label="Download markdown", visible=False)

        # Error panel — sibling of ``result_panel``, NOT a child. This
        # split is the #186 §6.1 fix: previously the ERROR branch
        # toggled ``result_panel`` (which carries the "Backlog target"
        # form as a child), so the user saw an actionable-looking form
        # connected to nothing on every pipeline failure.
        with gr.Group(visible=False) as error_panel:
            error_md = gr.Markdown("")

        # Shared export configuration. The four fields below drive BOTH
        # the deep-flow "Push to backlog" button AND the fan-out
        # "Create follow-up issues" button. Lifted to its own gated
        # group (#186 §R3 / §6.1) with an independent ``backlog_visible``
        # flag — visible on DONE and DONE_FANOUT, hidden on ERROR.
        with gr.Group(visible=False) as backlog_target_panel:
            gr.Markdown("---\n### Backlog target")
            export_backend = gr.Dropdown(
                choices=["jira", "github"],
                value="github",
                label="Backend",
            )
            export_target = gr.Textbox(
                label="Target (Jira: project key e.g. 'RTIA' | GitHub: repo 'owner/name')",
                value="augustineuzokwe/rtia",
            )
            export_extra = gr.Textbox(
                label=(
                    "Optional: Jira parent epic key (e.g. 'RTIA-1') OR "
                    "GitHub project number (e.g. '5')"
                ),
                value="",
            )
            export_dry_run = gr.Checkbox(label="Dry run (build payload, don't send)", value=True)

        # Deep-flow export trigger. Hidden on ``DONE_FANOUT`` because
        # the single-artifact endpoint has nothing to push in fan-out
        # mode (see ADR-0010). ``backlog_target_panel`` above stays
        # visible regardless so the fan-out user can still configure
        # the destination for the follow-up exports below.
        with gr.Group(visible=False) as deep_export_panel:
            gr.Markdown("### Push the deep-flow artifact")
            # Issue #208 — optional field. Leave blank to create a new
            # issue (current default). Set to an existing issue number /
            # key to PATCH that issue instead, collapsing a fan-out stub
            # into its deep deep-dive in place.
            export_update_id = gr.Textbox(
                label=(
                    "Update existing issue # (leave blank to create new) — "
                    "GitHub: issue number (e.g. '203') | Jira: issue key (e.g. 'RTIA-42')"
                ),
                value="",
            )
            export_btn = gr.Button("Push to backlog", variant="primary")
            export_result_md = gr.Markdown("")

        # Deferred-stories panel (Phase 15.3) — only visible when the
        # Analyst flagged multiple implied stories and the PO scoped
        # the main artifact to one of them.
        with gr.Group(visible=False) as deferred_panel:
            deferred_md = gr.Markdown("")
            deferred_checkboxes = gr.CheckboxGroup(
                choices=[],
                value=[],
                label="Create follow-up issues for these deferred stories:",
                visible=False,
            )
            export_deferred_btn = gr.Button("Create follow-up issues", variant="primary")
            export_deferred_result_md = gr.Markdown("")

        # ----- event handlers --------------------------------------

        # ``_SPREAD_KEYS`` and ``outputs`` are positionally-aligned by
        # convention; the tuple Gradio receives must hand each update
        # to the right component or the UI breaks silently (#186 §R1).
        # Sourcing both from a single keys list catches misalignment at
        # build time and is exercised by the alignment test in
        # tests/test_ui_state_panels.py.
        _SPREAD_KEYS = (
            "status_md",
            "thread_id_state",
            "po_visible",
            "po_questions",
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
            # Issue #207 — originals state (single key, list value).
            "po_fanout_originals",
        )

        def _spread(state):
            mapping = _state_to_panels(state)
            base = tuple(mapping[k] for k in _SPREAD_KEYS)
            # Issue #207 — flatten the per-row updates onto the tail of
            # the tuple, matching the positional order in ``outputs``
            # below. Each row contributes three components in
            # ``(row, checkbox, textbox)`` order.
            rows = mapping["po_fanout_rows"]
            flat_rows = tuple(item for row_tuple in rows for item in row_tuple)
            return base + flat_rows

        outputs = [
            status_md,
            thread_id_state,
            po_panel,
            po_questions,
            po_answers,
            po_paused_payload_state,
            review_panel,
            review_preview,
            result_panel,
            result_md,
            download_file,
            backlog_target_panel,
            deep_export_panel,
            deferred_panel,
            deferred_md,
            deferred_checkboxes,
            error_panel,
            error_md,
            run_btn,
            po_fanout_originals_state,
        ]
        # Issue #207 — append the N pre-built fan-out rows (3 components
        # each: Row container + Checkbox + Textbox) so handlers can
        # update every row from a single returned tuple.
        for _row, _chk, _txt in po_fanout_rows_components:
            outputs.extend([_row, _chk, _txt])
        assert len(outputs) == len(_SPREAD_KEYS) + 3 * _MAX_FANOUT_ROWS, (
            "outputs / _SPREAD_KEYS length mismatch — every spread key must "
            "have a positionally-matched Gradio component, plus 3 components "
            "per fan-out row"
        )

        def on_upload_pdf(f):
            if f is None:
                return gr.update(), ""
            try:
                with open(f.name, "rb") as fh:
                    text = extract_pdf(fh.read())
            except ScannedPdfError as exc:
                return gr.update(), f"❌ {exc}"
            except (ParseError, FileTooLargeError) as exc:
                return gr.update(), f"❌ {exc}"
            return gr.update(value=text), f"✅ Extracted {len(text)} characters from PDF."

        def on_upload_md(f):
            if f is None:
                return gr.update(), ""
            try:
                with open(f.name, "rb") as fh:
                    text = extract_markdown(fh.read())
            except ParseError as exc:
                return gr.update(), f"❌ {exc}"
            return gr.update(value=text), f"✅ Loaded {len(text)} characters of markdown."

        upload_pdf_input.change(on_upload_pdf, [upload_pdf_input], [req_text, upload_status])
        upload_md_input.change(on_upload_md, [upload_md_input], [req_text, upload_status])

        def _all_hidden_tuple(status_text: str, run_interactive: bool) -> tuple:
            """Build a panel-update tuple where every gated panel is hidden.

            Shared by the empty-input path and the in-flight "running…"
            path. Positionally aligned with ``_SPREAD_KEYS`` / ``outputs``
            (#186 §R1); the build-time assert below guards length drift.
            """
            base = (
                gr.update(value=status_text),  # status_md
                "",  # thread_id_state
                gr.update(visible=False),  # po_panel
                gr.update(value=""),  # po_questions
                gr.update(visible=True),  # po_answers
                {},  # po_paused_payload_state
                gr.update(visible=False),  # review_panel
                gr.update(value=""),  # review_preview
                gr.update(visible=False),  # result_panel
                gr.update(value=""),  # result_md
                gr.update(value=None, visible=False),  # download_file
                gr.update(visible=False),  # backlog_target_panel
                gr.update(visible=False),  # deep_export_panel
                gr.update(visible=False),  # deferred_panel
                gr.update(value=""),  # deferred_md
                gr.update(choices=[], value=[], visible=False),  # deferred_checkboxes
                gr.update(visible=False),  # error_panel
                gr.update(value=""),  # error_md
                gr.update(interactive=run_interactive),  # run_btn
                [],  # po_fanout_originals_state
            )
            # Issue #207 — append N hidden row triplets so the tuple
            # matches the extended ``outputs`` length.
            flat_rows = tuple(item for row_tuple in _hidden_fanout_rows() for item in row_tuple)
            return base + flat_rows

        def on_run(text):
            """Generator handler — yields a 'running' frame immediately so
            the user gets visible feedback during the 5-15s Analyst call,
            then yields the real state when the runner returns.

            Without this intermediate yield, the only feedback during the
            pipeline's first leg is the Run button greying out (PR #188
            §6.4) — a real but easy-to-miss signal. Yielding a
            'Status: running…' frame on click makes it unambiguous.
            See: feedback from the live UI walk-through (Epic #1 follow-up).
            """
            if not (text or "").strip():
                # Empty input — no thread is started, every gated panel
                # stays hidden, Run stays enabled. (#186 §6.2)
                yield _all_hidden_tuple("Status: **idle**", run_interactive=True)
                return
            # Immediate 'running' frame so the user sees feedback right
            # after click; Run button locked (matches #186 §6.4 contract
            # for active-thread states).
            yield _all_hidden_tuple(
                "Status: **running…** _(analysing requirement — this typically takes 5–15 s)_",
                run_interactive=False,
            )
            # Now block on the actual pipeline (Analyst → PO checkpoint
            # or deep flow). When it returns, _spread emits the final
            # panel set.
            state = _runner(app).start(text)
            yield _spread(state)

        run_btn.click(on_run, [req_text], outputs)

        def on_po_submit(
            thread_id,
            answers_blob,
            paused_payload,
            originals,
            _text,
            *fanout_row_values,
        ):
            """Submit handler dispatches on paused payload's mode.

            Fan-out (Issue #207): variadic ``*fanout_row_values`` is the
            flat ``[chk_0, chk_1, …, chk_{N-1}, txt_0, txt_1, …,
            txt_{N-1}]`` tuple, one Checkbox + one Textbox per row slot.
            We zip the kept (checked) rows against the ``originals``
            snapshot to build the structured ``selected_stories`` resume
            payload — each item carries the edited title, the matching
            Analyst-provided summary, and the original-title pointer the
            graph uses to look summary up. Empty selection ⇒ ``[]``,
            which the graph treats as "fan out everything".

            Deep: today's flat ``dict[question, answer]`` shape.
            """
            runner = _runner(app)
            mode = (paused_payload or {}).get("mode", "deep")
            questions = (paused_payload or {}).get("critical_ambiguities") or []
            lines = [ln.strip() for ln in (answers_blob or "").splitlines() if ln.strip()]
            answers = {
                q: (lines[i] if i < len(lines) else "no answer given")
                for i, q in enumerate(questions)
            }
            if mode == "fan_out":
                checks = list(fanout_row_values[:_MAX_FANOUT_ROWS])
                texts = list(fanout_row_values[_MAX_FANOUT_ROWS : 2 * _MAX_FANOUT_ROWS])
                stories_payload = (paused_payload or {}).get("implied_stories") or []
                originals_list = list(originals or [])
                # Build a lookup for the Analyst-provided summary from the
                # original title so renamed rows still get their summary.
                summary_by_original = {
                    (s.get("title") or "").strip().lower(): (s.get("summary") or "")
                    for s in stories_payload
                }
                selected_stories: list[dict[str, str]] = []
                for i, (checked, edited_title) in enumerate(zip(checks, texts, strict=False)):
                    if not bool(checked):
                        continue
                    title = (edited_title or "").strip()
                    if not title:
                        continue
                    original = originals_list[i] if i < len(originals_list) else title
                    summary = summary_by_original.get(original.strip().lower(), "")
                    selected_stories.append(
                        {
                            "title": title,
                            "summary": summary,
                            "original_title": original,
                        }
                    )
                resume_value = {
                    "selected_stories": selected_stories,
                    "answers": answers,
                }
            else:
                resume_value = answers
            state = runner.resume(thread_id, resume_value)
            return _spread(state)

        # Inputs: fixed leading args + flat row values
        # (all checkboxes first, then all textboxes).
        _po_inputs = [
            thread_id_state,
            po_answers,
            po_paused_payload_state,
            po_fanout_originals_state,
            req_text,
        ]
        _po_inputs.extend(chk for _row, chk, _txt in po_fanout_rows_components)
        _po_inputs.extend(txt for _row, _chk, txt in po_fanout_rows_components)
        po_submit.click(on_po_submit, _po_inputs, outputs)

        def on_review_accept(thread_id):
            state = _runner(app).resume(thread_id, {"accepted": True})
            return _spread(state)

        def on_review_override(thread_id, desc, obj):
            state = _runner(app).resume(
                thread_id,
                {"accepted": False, "description": desc or "", "objective": obj or ""},
            )
            return _spread(state)

        review_accept.click(on_review_accept, [thread_id_state], outputs)

        def on_export(thread_id, backend, target, extra, dry_run, update_id):
            """Push the current thread's artifact to Jira or GitHub.

            All configurable fields are typed into one Gradio Textbox
            each to keep the UI simple. The handler shapes them into
            an ``ExportTarget`` for the correct backend.

            Issue #208 — when ``update_id`` is non-empty, the handler
            calls ``exporter.update_issue`` instead of ``exporter.export``
            so the artifact replaces an existing issue rather than
            creating a duplicate. The success message says "Updated"
            vs "Pushed" so the PO sees which path ran.
            """
            loaded = _runner(app).get_artifact_and_title(thread_id)
            if loaded is None:
                return "❌ No final artifact for this thread yet."
            artifact, title = loaded

            update_id = (update_id or "").strip()
            tgt = _build_export_target(backend, target, extra)

            try:
                exporter = make_exporter(backend)
                if update_id:
                    result = exporter.update_issue(
                        update_id,
                        artifact.as_markdown(),
                        tgt,
                        title=title,
                        dry_run=bool(dry_run),
                    )
                else:
                    result = exporter.export(
                        artifact.as_markdown(), tgt, title=title, dry_run=bool(dry_run)
                    )
            except ExportConfigError as exc:
                return f"❌ Config error: {exc}"
            except ExporterTransportError as exc:
                return f"❌ Transport error: {exc}"

            verb = "update" if update_id else "create"
            if result.dry_run:
                import json as _json

                payload_json = _json.dumps(result.payload, indent=2)[:2000]
                return (
                    f"✅ Dry-run for **{result.backend}** ({verb}). Title: `{title}`\n\n"
                    f"```json\n{payload_json}\n```"
                )
            if not result.success:
                return f"❌ {result.backend} {verb} failed: {result.error}"
            action = "Updated" if update_id else "Pushed to"
            return f"✅ {action} {result.backend}: [{result.key}]({result.url})"

        export_btn.click(
            on_export,
            [
                thread_id_state,
                export_backend,
                export_target,
                export_extra,
                export_dry_run,
                export_update_id,
            ],
            [export_result_md],
        )

        def on_export_deferred(thread_id, backend, target, extra, dry_run, selected_titles):
            """Batch-create follow-up issues for the deferred OR fan-out stories.

            Reuses the same backend dropdown + target fields from the
            single-export form. ``selected_titles`` is the checkbox-group
            value (a subset of the visible titles). Empty selection ⇒
            create issues for ALL stories in the active list.

            Dispatch mirrors the API ``/export-deferred`` endpoint: on a
            ``DONE_FANOUT`` thread the source list is ``fan_out_stories``;
            otherwise it's the deferred-implied list. Both are the same
            ``ImpliedStory`` shape so the rest of the loop is identical.
            """
            if not (thread_id or "").strip():
                # Defensive: if the Gradio State component is empty (no
                # pipeline run in this session, or state was reset), say
                # so explicitly. Without this guard the runner returns a
                # RUNNING placeholder for the empty id, which would
                # silently fall through to the misleading "no deferred
                # stories" message.
                return "❌ No active thread — start a pipeline run first."
            runner = _runner(app)
            current = runner.get_state(thread_id)
            loaded = select_followup_source(runner, thread_id, current.status)
            if loaded is None:
                return "❌ No thread state."
            deferred, requirement_text = loaded
            if not deferred:
                return followup_empty_message(current.status)

            include_titles = selected_titles or [s.title for s in deferred]

            tgt = _build_export_target(backend, target, extra)

            try:
                exporter = make_exporter(backend)
            except ExportConfigError as exc:
                return f"❌ Config error: {exc}"

            include_lower = {t.lower() for t in include_titles}
            lines: list[str] = []
            for story in deferred:
                if story.title.lower() not in include_lower:
                    continue
                body = build_followup_markdown(
                    story.title, story.summary, requirement_excerpt=requirement_text
                )
                try:
                    result = exporter.export(body, tgt, title=story.title, dry_run=bool(dry_run))
                except (ExportConfigError, ExporterTransportError) as exc:
                    lines.append(f"- ❌ **{story.title}**: {exc}")
                    continue
                if result.dry_run:
                    lines.append(f"- 📋 **{story.title}** (dry-run, would push)")
                elif result.success:
                    if result.url:
                        lines.append(f"- ✅ **{story.title}** → [{result.key}]({result.url})")
                    else:
                        lines.append(f"- ✅ **{story.title}** (no URL returned)")
                else:
                    lines.append(f"- ❌ **{story.title}**: {result.error}")

            return "\n".join(lines) if lines else "_Nothing exported._"

        export_deferred_btn.click(
            on_export_deferred,
            [
                thread_id_state,
                export_backend,
                export_target,
                export_extra,
                export_dry_run,
                deferred_checkboxes,
            ],
            [export_deferred_result_md],
        )
        review_override.click(
            on_review_override,
            [thread_id_state, override_description, override_objective],
            outputs,
        )

    return blocks


__all__ = ["build_blocks"]
