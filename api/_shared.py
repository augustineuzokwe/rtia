"""Shared helpers used by both the FastAPI route layer (``api/main.py``)
and the Gradio UI handlers (``ui/gradio_app.py``).

Background — risk **R2** in
[docs/ui_audit_2026-05-24.md](../docs/ui_audit_2026-05-24.md). Before
this module existed, both surfaces re-implemented the same dispatch
logic (deferred-follow-up markdown body, DONE / DONE_FANOUT story-list
selection) against the same underlying ``PipelineRunner``. The shapes
were identical; the prior docstrings called out the duplication and
attributed it to a circular-import concern that this module — a
neutral third file both can import — resolves.

Anything added here should have **at least two callers**. Single-caller
helpers belong in their owning module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from api.models import ThreadStatus

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids import at runtime
    from api.runner import PipelineRunner


def build_followup_markdown(
    story_title: str,
    story_summary: str,
    *,
    requirement_excerpt: str,
    max_excerpt_chars: int = 800,
) -> str:
    """Compose a follow-up-issue body for a deferred/fan-out implied story.

    Intentionally lightweight — these are *placeholder* issues a PO
    triages later. They're not full RTIA artifacts. Each carries enough
    context (title, summary, originating-requirement excerpt) for the
    PO to re-run RTIA on the title once they want to flesh it out.

    Used by both ``POST /pipeline/{id}/export-deferred`` and the UI's
    "Create follow-up issues" button. Output must stay byte-identical
    across surfaces so the same backlog item looks the same whether the
    PO clicked or curl'd.
    """
    excerpt = (requirement_excerpt or "").strip()
    if len(excerpt) > max_excerpt_chars:
        excerpt = excerpt[:max_excerpt_chars].rstrip() + "…"
    parts = [
        f"## {story_title}",
        "",
        story_summary,
        "",
        "## Provenance",
        (
            "_Deferred from an RTIA run on a multi-story requirement. "
            "Re-run RTIA on this title once you're ready to flesh out the "
            "full Description / Objective / ACs / Test Cases._"
        ),
        "",
        (
            '_When you push the deep artifact, set **"Update existing '
            "issue #\"** to **this issue's number** so the deep dive "
            "replaces this stub instead of creating a duplicate (#208)._"
        ),
    ]
    if excerpt:
        parts.extend(["", "## Originating requirement (excerpt)", "", excerpt])
    return "\n".join(parts)


def select_followup_source(
    runner: PipelineRunner,
    thread_id: str,
    status: ThreadStatus,
) -> tuple[list[Any], str] | None:
    """Pick the right story source for the follow-up-export dispatch.

    On ``DONE_FANOUT`` threads the source is ``fan_out_stories``; on
    every other status it's the deferred-implied list. Both paths
    return ``ImpliedStory``-shaped objects so callers iterate identically.

    Returns ``(stories, requirement_text)`` or ``None`` if the thread
    has no state. The empty-list case (thread exists but produced no
    follow-ups) returns ``([], "")`` — callers decide how to surface
    "nothing to do" since the wording differs between API and UI.
    """
    if status == ThreadStatus.DONE_FANOUT:
        return runner.get_fanout_stories_and_context(thread_id)
    return runner.get_deferred_stories_and_context(thread_id)


def followup_empty_message(status: ThreadStatus) -> str:
    """Human-readable "no follow-up stories to push" copy.

    UI-only today (the API returns an empty
    ``DeferredExportResponse``), but lives here so the wording matches
    the source-selection dispatch above — both pivot on
    ``DONE_FANOUT`` vs everything-else. If the API ever wants to
    surface a message field, it pulls from the same place.
    """
    if status == ThreadStatus.DONE_FANOUT:
        return "_No fan-out stories — nothing to create._"
    return "_No deferred stories — nothing to create._"


__all__ = [
    "build_followup_markdown",
    "select_followup_source",
    "followup_empty_message",
]
