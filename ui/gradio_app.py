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
from exporters.base import (
    ExportConfigError,
    ExporterTransportError,
    ExportTarget,
    make_exporter,
)


def _select_followup_source(
    runner: PipelineRunner, thread_id: str, status: ThreadStatus
) -> tuple[tuple[list[Any], str] | None, str]:
    """Pick the right story source for the follow-up export handler.

    Mirrors the dispatch in ``api.main.export_deferred`` so the UI button
    and the API endpoint surface the same stories: ``fan_out_stories`` on
    a ``DONE_FANOUT`` thread, otherwise the deferred-implied list. Both
    paths return ``ImpliedStory``-shaped objects so the caller's loop is
    identical.

    Returns ``(loaded, empty_message)`` — ``loaded`` is either
    ``(stories, requirement_text)`` or ``None`` (no thread state), and
    ``empty_message`` is the human-readable string to show when
    ``stories`` is empty. The two paths use different empty-state wording
    so the user sees terminology that matches the visible panel.
    """
    if status == ThreadStatus.DONE_FANOUT:
        return (
            runner.get_fanout_stories_and_context(thread_id),
            "_No fan-out stories — nothing to create._",
        )
    return (
        runner.get_deferred_stories_and_context(thread_id),
        "_No deferred stories — nothing to create._",
    )


def _build_followup_markdown(title: str, summary: str, requirement_text: str) -> str:
    """Compose the body of a deferred follow-up issue.

    Mirrors ``api.main._build_deferred_followup_markdown`` so the UI
    in-process path and the API endpoint produce identical issue bodies.
    Kept in two places because both consumers live behind their own
    surface; consolidating would require a circular-import dance.
    """
    excerpt = (requirement_text or "").strip()
    if len(excerpt) > 800:
        excerpt = excerpt[:800].rstrip() + "…"
    parts = [
        f"## {title}",
        "",
        summary,
        "",
        "## Provenance",
        (
            "_Deferred from an RTIA run on a multi-story requirement. "
            "Re-run RTIA on this title once you're ready to flesh out the "
            "full Description / Objective / ACs / Test Cases._"
        ),
    ]
    if excerpt:
        parts.extend(["", "## Originating requirement (excerpt)", "", excerpt])
    return "\n".join(parts)


def _runner(app: FastAPI) -> PipelineRunner:
    return app.state.runner


def _state_to_panels(state) -> dict[str, Any]:
    """Map a ThreadState onto Gradio update objects for each panel.

    Returned as a dict the caller spreads into the relevant outputs;
    keeping the mapping in one place keeps the event handlers below
    readable.
    """
    status_label = f"Status: **{state.status.value}**"
    base: dict[str, Any] = {
        "status_md": status_label,
        "thread_id_state": state.thread_id,
        "po_visible": gr.update(visible=False),
        "review_visible": gr.update(visible=False),
        "result_visible": gr.update(visible=False),
        # Phase 16 hotfix — deep-flow export form has its own visibility so
        # it stays hidden on DONE_FANOUT (where no deep artifact exists).
        # See ADR-0010 and issue #175 for context.
        "deep_export_visible": gr.update(visible=False),
        "po_questions": gr.update(value=""),
        "po_answers_visible": gr.update(visible=True),
        "po_fanout_visible": gr.update(visible=False),
        "po_fanout_checkboxes": gr.update(choices=[], value=[], visible=False),
        "po_paused_payload": {},
        "review_preview": gr.update(value=""),
        "result_md": gr.update(value=""),
        "download_file": gr.update(value=None, visible=False),
        "deferred_visible": gr.update(visible=False),
        "deferred_md": gr.update(value=""),
        "deferred_checkboxes": gr.update(choices=[], value=[], visible=False),
        "deferred_titles": [],
    }

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
            base["po_fanout_visible"] = gr.update(visible=True)
            base["po_fanout_checkboxes"] = gr.update(
                choices=[s["title"] for s in stories],
                value=[s["title"] for s in stories],
                visible=True,
            )
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
            base["po_fanout_visible"] = gr.update(visible=False)
    elif state.status == ThreadStatus.PAUSED_REVIEW:
        base["review_visible"] = gr.update(visible=True)
        base["review_preview"] = gr.update(value=state.payload.get("rendered_artifact", ""))
    elif state.status == ThreadStatus.DONE:
        rendered = state.payload.get("rendered_artifact", "")
        base["result_visible"] = gr.update(visible=True)
        # Deep flow produced an artifact — the single-artifact "Push to
        # backlog" form is the right control here.
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
        base["deferred_titles"] = [s["title"] for s in deferred]
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
        # No deep artifact in fan-out mode — keep the single-artifact
        # "Push to backlog" form hidden so the PO uses the correct
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
        base["deferred_visible"] = gr.update(visible=bool(stubs))
        if stubs:
            base["deferred_md"] = gr.update(value="### Push these to the backlog:")
            base["deferred_checkboxes"] = gr.update(
                choices=[s["title"] for s in stubs],
                value=[s["title"] for s in stubs],
                visible=True,
            )
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
            # Phase 15.4 — CheckboxGroup for the fan-out case. Hidden in
            # deep mode; populated from the paused payload's implied_stories.
            po_fanout_checkboxes = gr.CheckboxGroup(
                choices=[],
                value=[],
                label="Stories to push as backlog stubs (uncheck to drop)",
                visible=False,
            )
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

            # Deep-flow export form. Wrapped in its own Group so its
            # visibility is independent from the artifact display —
            # ``DONE_FANOUT`` reuses ``result_panel`` for the fan-out
            # stub list but must NOT expose this form (the single-
            # artifact endpoint has nothing to push in fan-out mode).
            # See issue #175 / ADR-0010.
            with gr.Group(visible=False) as deep_export_panel:
                gr.Markdown("---\n### Push to backlog")
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
                export_dry_run = gr.Checkbox(
                    label="Dry run (build payload, don't send)", value=True
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

        def _spread(state):
            mapping = _state_to_panels(state)
            return (
                mapping["status_md"],
                mapping["thread_id_state"],
                mapping["po_visible"],
                mapping["po_questions"],
                mapping["po_fanout_checkboxes"],
                mapping["po_answers_visible"],
                mapping["po_paused_payload"],
                mapping["review_visible"],
                mapping["review_preview"],
                mapping["result_visible"],
                mapping["result_md"],
                mapping["download_file"],
                mapping["deep_export_visible"],
                mapping["deferred_visible"],
                mapping["deferred_md"],
                mapping["deferred_checkboxes"],
            )

        outputs = [
            status_md,
            thread_id_state,
            po_panel,
            po_questions,
            po_fanout_checkboxes,
            po_answers,
            po_paused_payload_state,
            review_panel,
            review_preview,
            result_panel,
            result_md,
            download_file,
            deep_export_panel,
            deferred_panel,
            deferred_md,
            deferred_checkboxes,
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

        def on_po_submit(thread_id, answers_blob, fanout_selection, paused_payload, _text):
            """Phase 15.4 — submit handler dispatches on paused payload's mode.

            Fan-out: bundle ``selected_story_titles`` + free-text answers
            (for any non-story critical questions) into the structured
            resume value the graph expects.

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
                resume_value = {
                    "selected_story_titles": list(fanout_selection or []),
                    "answers": answers,
                }
            else:
                resume_value = answers
            state = runner.resume(thread_id, resume_value)
            return _spread(state)

        po_submit.click(
            on_po_submit,
            [
                thread_id_state,
                po_answers,
                po_fanout_checkboxes,
                po_paused_payload_state,
                req_text,
            ],
            outputs,
        )

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

        def on_export(thread_id, backend, target, extra, dry_run):
            """Push the current thread's artifact to Jira or GitHub.

            All three configurable fields are typed into one Gradio
            Textbox each to keep the UI simple. The handler shapes them
            into an ``ExportTarget`` for the correct backend.
            """
            loaded = _runner(app).get_artifact_and_title(thread_id)
            if loaded is None:
                return "❌ No final artifact for this thread yet."
            artifact, title = loaded

            target = (target or "").strip()
            extra = (extra or "").strip()
            if backend == "jira":
                tgt = ExportTarget(
                    backend="jira",
                    jira_project_key=target or None,
                    jira_parent_key=extra or None,
                )
            else:
                tgt = ExportTarget(
                    backend="github",
                    github_repo=target or None,
                    github_project_number=int(extra) if extra.isdigit() else None,
                )

            try:
                exporter = make_exporter(backend)
                result = exporter.export(
                    artifact.as_markdown(), tgt, title=title, dry_run=bool(dry_run)
                )
            except ExportConfigError as exc:
                return f"❌ Config error: {exc}"
            except ExporterTransportError as exc:
                return f"❌ Transport error: {exc}"

            if result.dry_run:
                import json as _json

                payload_json = _json.dumps(result.payload, indent=2)[:2000]
                return (
                    f"✅ Dry-run for **{result.backend}**. Title: `{title}`\n\n"
                    f"```json\n{payload_json}\n```"
                )
            if not result.success:
                return f"❌ {result.backend} export failed: {result.error}"
            return f"✅ Pushed to {result.backend}: [{result.key}]({result.url})"

        export_btn.click(
            on_export,
            [
                thread_id_state,
                export_backend,
                export_target,
                export_extra,
                export_dry_run,
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
            loaded, empty_message = _select_followup_source(runner, thread_id, current.status)
            if loaded is None:
                return "❌ No thread state."
            deferred, requirement_text = loaded
            if not deferred:
                return empty_message

            include_titles = selected_titles or [s.title for s in deferred]

            target = (target or "").strip()
            extra = (extra or "").strip()
            if backend == "jira":
                tgt = ExportTarget(
                    backend="jira",
                    jira_project_key=target or None,
                    jira_parent_key=extra or None,
                )
            else:
                tgt = ExportTarget(
                    backend="github",
                    github_repo=target or None,
                    github_project_number=int(extra) if extra.isdigit() else None,
                )

            try:
                exporter = make_exporter(backend)
            except ExportConfigError as exc:
                return f"❌ Config error: {exc}"

            include_lower = {t.lower() for t in include_titles}
            lines: list[str] = []
            for story in deferred:
                if story.title.lower() not in include_lower:
                    continue
                body = _build_followup_markdown(story.title, story.summary, requirement_text)
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


class _IdleState:
    """Placeholder ThreadState-shaped object for the pre-run UI."""

    status = ThreadStatus.RUNNING  # the panels treat anything non-paused as idle
    thread_id = ""
    payload: dict = {}


_RUNNER_IDLE_STATE = _IdleState()


__all__ = ["build_blocks"]
