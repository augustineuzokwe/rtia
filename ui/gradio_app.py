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

from api.models import ThreadStatus
from api.parsers import (
    FileTooLargeError,
    ParseError,
    ScannedPdfError,
    extract_markdown,
    extract_pdf,
)
from api.runner import PipelineRunner


def _runner(app: FastAPI) -> PipelineRunner:
    return app.state.runner


def _state_to_panels(state) -> dict[str, Any]:
    """Map a ThreadState onto Gradio update objects for each panel.

    Returned as a dict the caller spreads into the relevant outputs;
    keeping the mapping in one place keeps the event handlers below
    readable.
    """
    status_label = f"Status: **{state.status.value}**"
    base = {
        "status_md": status_label,
        "thread_id_state": state.thread_id,
        "po_visible": gr.update(visible=False),
        "review_visible": gr.update(visible=False),
        "result_visible": gr.update(visible=False),
        "po_questions": gr.update(value=""),
        "review_preview": gr.update(value=""),
        "result_md": gr.update(value=""),
        "download_file": gr.update(value=None, visible=False),
    }

    if state.status == ThreadStatus.PAUSED_PO:
        questions = state.payload.get("critical_ambiguities", [])
        formatted = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
        base["po_visible"] = gr.update(visible=True)
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
        base["result_md"] = gr.update(value=rendered)
        # write a temp .md the user can click to download
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(rendered)
            base["download_file"] = gr.update(value=fh.name, visible=True)
    elif state.status == ThreadStatus.ERROR:
        rendered = state.payload.get("rendered_artifact", "")
        base["result_visible"] = gr.update(visible=True)
        base["result_md"] = gr.update(value=f"**Pipeline error.**\n\n{rendered}")
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

        with gr.Row(), gr.Column(scale=2):
            req_text = gr.Textbox(
                label="Requirement text", lines=12, placeholder="Paste raw requirements here…"
            )
            upload_pdf_input = gr.File(label="…or upload a PDF", file_types=[".pdf"])
            upload_md_input = gr.File(label="…or upload Markdown", file_types=[".md"])
            run_btn = gr.Button("Run pipeline", variant="primary")
            upload_status = gr.Markdown("")

        # PO checkpoint panel.
        with gr.Group(visible=False) as po_panel:
            po_questions = gr.Markdown("")
            po_answers = gr.Textbox(
                label="Answers (one per line, in order)",
                lines=4,
                placeholder="Answer 1\nAnswer 2\n…",
            )
            po_submit = gr.Button("Submit answers", variant="primary")

        # Story review panel.
        with gr.Group(visible=False) as review_panel:
            review_preview = gr.Markdown("")
            with gr.Row():
                review_accept = gr.Button("Accept as-is", variant="primary")
                review_override = gr.Button("Override")
            override_description = gr.Textbox(
                label="New description (leave blank to keep)", lines=2, visible=True
            )
            override_objective = gr.Textbox(
                label="New objective (leave blank to keep)", lines=2, visible=True
            )

        # Result panel.
        with gr.Group(visible=False) as result_panel:
            result_md = gr.Markdown("")
            download_file = gr.File(label="Download markdown", visible=False)

        # ----- event handlers --------------------------------------

        def _spread(state):
            mapping = _state_to_panels(state)
            return (
                mapping["status_md"],
                mapping["thread_id_state"],
                mapping["po_visible"],
                mapping["po_questions"],
                mapping["review_visible"],
                mapping["review_preview"],
                mapping["result_visible"],
                mapping["result_md"],
                mapping["download_file"],
            )

        outputs = [
            status_md,
            thread_id_state,
            po_panel,
            po_questions,
            review_panel,
            review_preview,
            result_panel,
            result_md,
            download_file,
        ]

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

        def on_run(text):
            if not (text or "").strip():
                return _spread(_RUNNER_IDLE_STATE)
            state = _runner(app).start(text)
            return _spread(state)

        run_btn.click(on_run, [req_text], outputs)

        def on_po_submit(thread_id, answers_blob, text):
            runner = _runner(app)
            current = runner.get_state(thread_id)
            questions = current.payload.get("critical_ambiguities", [])
            lines = [ln.strip() for ln in (answers_blob or "").splitlines() if ln.strip()]
            answers = {
                q: (lines[i] if i < len(lines) else "no answer given")
                for i, q in enumerate(questions)
            }
            state = runner.resume(thread_id, answers)
            return _spread(state)

        po_submit.click(on_po_submit, [thread_id_state, po_answers, req_text], outputs)

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
        review_override.click(
            on_review_override,
            [thread_id_state, override_description, override_objective],
            outputs,
        )

    return blocks


class _IdleState:
    """Placeholder ThreadState-shaped object for the pre-run UI."""

    status = ThreadStatus.RUNNING  # the panels treat anything non-paused as idle
    thread_id = ""
    payload: dict = {}


_RUNNER_IDLE_STATE = _IdleState()


__all__ = ["build_blocks"]
