"""PipelineRunner — single-process wrapper around the compiled LangGraph.

Each process holds one compiled pipeline (the SQLite checkpointer survives
restarts; threads survive too via their persisted state). Three methods —
``start``, ``resume``, ``get_state`` — map cleanly onto the demo's existing
control flow in ``scripts/run_pipeline_demo.py``. The API endpoints and
the Gradio UI both call these.

Status mapping (see ``api.models.ThreadStatus``):

* LangGraph returned without ``__interrupt__`` and ``final_artifact`` is
  populated → ``DONE``.
* LangGraph returned with ``__interrupt__`` whose payload contains
  ``critical_ambiguities`` → ``PAUSED_PO``.
* LangGraph returned with ``__interrupt__`` whose payload contains
  ``rendered_artifact`` → ``PAUSED_REVIEW``.
* ``PipelineStepError`` raised → ``ERROR`` (build the stub artifact, but
  surface the error to the caller).
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.types import Command

from agents._llm_errors import PipelineStepError
from agents.graph import (
    build_pipeline,
    build_stub_artifact_from_error,
    deferred_implied_stories,
)
from api.models import ThreadState, ThreadStatus


class PipelineRunner:
    """Owns one compiled pipeline and translates between the API + LangGraph."""

    def __init__(self, pipeline: Any | None = None) -> None:
        """``pipeline=None`` builds the production default (SQLite saver).

        Tests pass an in-memory-checkpointed compiled graph in.
        """
        self._pipeline = pipeline if pipeline is not None else build_pipeline()

    @staticmethod
    def _new_thread_id() -> str:
        # Short, URL-safe, collision-resistant enough for a localhost dev tool.
        return uuid.uuid4().hex

    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, requirement_text: str, thread_id: str | None = None) -> ThreadState:
        """Kick off a new run. Blocks until first pause or completion.

        ``PipelineStepError`` is caught and surfaced as ``ThreadStatus.ERROR``
        with a rendered stub artifact in the payload — symmetric with how
        the demo handles it.
        """
        tid = thread_id or self._new_thread_id()
        try:
            result = self._pipeline.invoke(
                {"requirement_text": requirement_text}, config=self._config(tid)
            )
        except PipelineStepError as exc:
            return self._error_state(tid, exc)
        return self._translate(tid, result)

    def resume(self, thread_id: str, resume_value: Any) -> ThreadState:
        """Resume a paused thread. Blocks until next pause or completion."""
        try:
            result = self._pipeline.invoke(
                Command(resume=resume_value), config=self._config(thread_id)
            )
        except PipelineStepError as exc:
            return self._error_state(thread_id, exc)
        return self._translate(thread_id, result)

    def get_state(self, thread_id: str) -> ThreadState:
        """Snapshot a thread's current state without advancing it.

        Useful for the GET endpoint and for the UI to reconnect after a
        page reload. Reads the LangGraph checkpointer directly — no LLM
        call, free.
        """
        snapshot = self._pipeline.get_state(self._config(thread_id))
        values: dict[str, Any] = snapshot.values or {}

        # next is a tuple of pending node names; non-empty + an interrupt
        # in tasks ⇒ the thread is paused.
        interrupts = []
        for task in snapshot.tasks or ():
            interrupts.extend(getattr(task, "interrupts", ()) or ())

        if interrupts:
            payload = interrupts[0].value
            return self._paused_state(thread_id, payload)

        # Phase 15.4 — split terminal state precedes the deep one because
        # final_artifact is never populated in split runs. Use key-presence
        # not truthiness: a split thread that filtered to zero matches still
        # produced an empty split_stories list, and that's still a split
        # terminal state — not a deep failure.
        if "split_stories" in values:
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.DONE_SPLIT,
                payload={
                    "split_stories": [s.model_dump() for s in values["split_stories"]],
                    "po_answers": dict(values.get("po_answers") or {}),
                },
            )

        if "final_artifact" in values:
            artifact = values["final_artifact"]
            review = values.get("review_report")
            if artifact and isinstance(artifact.metadata, dict) and artifact.metadata.get("error"):
                return ThreadState(
                    thread_id=thread_id,
                    status=ThreadStatus.ERROR,
                    payload={
                        "final_artifact": artifact.model_dump(),
                        "rendered_artifact": artifact.as_markdown(),
                    },
                )
            deferred = deferred_implied_stories(values)
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.DONE,
                payload={
                    "final_artifact": artifact.model_dump(),
                    "rendered_artifact": artifact.as_markdown(),
                    "review_report": review.model_dump() if review is not None else None,
                    "deferred_stories": [s.model_dump() for s in deferred],
                },
            )

        # No interrupts, no final artifact — either fresh thread we don't
        # know about, or mid-run (shouldn't happen because invoke is
        # blocking, but exposed for completeness).
        return ThreadState(thread_id=thread_id, status=ThreadStatus.RUNNING, payload={})

    def render_markdown(self, thread_id: str) -> str | None:
        """Return the FinalUserStory's rendered markdown, or None if absent."""
        snapshot = self._pipeline.get_state(self._config(thread_id))
        values: dict[str, Any] = snapshot.values or {}
        artifact = values.get("final_artifact")
        if artifact is None:
            return None
        return artifact.as_markdown()

    def get_split_stories_and_context(self, thread_id: str) -> tuple[list[Any], str] | None:
        """Return ``(split_stories, requirement_text)`` for split threads.

        Phase 15.4 sibling of :meth:`get_deferred_stories_and_context`.
        Returns ``None`` if no state at all, OR if the thread is not a
        split thread (key absent). Returns ``([], requirement_text)``
        when the thread is split but the PO filtered to zero stories
        — distinct from "not a split thread" so the endpoint can
        report the empty-result case cleanly.
        """
        snapshot = self._pipeline.get_state(self._config(thread_id))
        values: dict[str, Any] = snapshot.values or {}
        if "split_stories" not in values:
            return None
        return list(values["split_stories"]), values.get("requirement_text", "")

    def get_deferred_stories_and_context(self, thread_id: str) -> tuple[list[Any], str] | None:
        """Return ``(deferred_stories, requirement_text)`` for the export-deferred endpoint.

        Returns ``None`` if no thread state exists. ``deferred_stories``
        is empty when nothing was deferred (single-feature requirement,
        or the PO never paused). ``requirement_text`` is included so the
        endpoint can attribute each follow-up issue back to the originating
        requirement.
        """
        snapshot = self._pipeline.get_state(self._config(thread_id))
        values: dict[str, Any] = snapshot.values or {}
        if not values:
            return None
        deferred = deferred_implied_stories(values)
        requirement_text = values.get("requirement_text", "")
        return deferred, requirement_text

    def get_artifact_and_title(self, thread_id: str) -> tuple[Any, str] | None:
        """Return ``(artifact, title)`` for the export endpoint, or None if absent.

        The export endpoint needs both the markdown body AND a title.
        The title comes from the user story's description: typically
        "As a <role>, I want <feature>…" — we trim to the "I want"
        clause for a punchier issue title (Jira/GitHub display long
        summaries truncated anyway).
        """
        snapshot = self._pipeline.get_state(self._config(thread_id))
        values: dict[str, Any] = snapshot.values or {}
        artifact = values.get("final_artifact")
        if artifact is None:
            return None
        title = _derive_title(artifact.description)
        return artifact, title

    # ----- internals --------------------------------------------------

    def _translate(self, thread_id: str, result: dict) -> ThreadState:
        """Map a raw LangGraph invoke result to a ``ThreadState``."""
        interrupts = result.get("__interrupt__")
        if interrupts:
            return self._paused_state(thread_id, interrupts[0].value)
        return self._done_state(thread_id, result)

    @staticmethod
    def _paused_state(thread_id: str, payload: dict) -> ThreadState:
        # Phase 15.4 — split PO checkpoint payload carries `mode == "split"`
        # plus the implied-stories list (for the CheckboxGroup) and any
        # remaining non-story critical questions (for text input).
        if payload.get("mode") == "split":
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.PAUSED_PO,
                payload={
                    "mode": "split",
                    "implied_stories": list(payload.get("implied_stories") or []),
                    "critical_ambiguities": list(payload.get("critical_ambiguities") or []),
                },
            )
        if "critical_ambiguities" in payload:
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.PAUSED_PO,
                payload={
                    "mode": "deep",
                    "critical_ambiguities": list(payload["critical_ambiguities"]),
                },
            )
        if "rendered_artifact" in payload:
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.PAUSED_REVIEW,
                payload={
                    "rendered_artifact": payload["rendered_artifact"],
                    "description": payload.get("description", ""),
                    "objective": payload.get("objective", ""),
                    "assumptions": list(payload.get("assumptions", [])),
                },
            )
        raise RuntimeError(f"Unknown interrupt payload shape: {sorted(payload)}")

    @staticmethod
    def _done_state(thread_id: str, result: dict) -> ThreadState:
        # Phase 15.4 — split branch terminates without producing a
        # FinalUserStory. Translate that into a DONE_SPLIT state. Use
        # key-presence not truthiness: empty list is still a split
        # terminal state (filtered to zero matches), not a deep failure.
        if "split_stories" in result:
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.DONE_SPLIT,
                payload={
                    "split_stories": [s.model_dump() for s in result["split_stories"]],
                    "po_answers": dict(result.get("po_answers") or {}),
                },
            )
        artifact = result["final_artifact"]
        review = result.get("review_report")
        deferred = deferred_implied_stories(result)
        return ThreadState(
            thread_id=thread_id,
            status=ThreadStatus.DONE,
            payload={
                "final_artifact": artifact.model_dump(),
                "rendered_artifact": artifact.as_markdown(),
                "review_report": review.model_dump() if review is not None else None,
                "deferred_stories": [s.model_dump() for s in deferred],
            },
        )

    @staticmethod
    def _error_state(thread_id: str, exc: PipelineStepError) -> ThreadState:
        stub = build_stub_artifact_from_error(exc)
        return ThreadState(
            thread_id=thread_id,
            status=ThreadStatus.ERROR,
            payload={
                "error": exc.detail.__dict__,
                "final_artifact": stub.model_dump(),
                "rendered_artifact": stub.as_markdown(),
            },
        )


_TITLE_SOFT_CAP = 60
"""Aim for backlog-issue titles around this length — fits Jira / GitHub
list views without truncation."""

_TITLE_HARD_CAP = 120
"""Absolute upper bound. Used as a fallback when word-boundary breaks
don't fire and as the historical contract for downstream consumers."""

# Subordinate-clause openers we cut at first occurrence to shorten
# verbose Story Writer descriptions. Order matters: we scan in order
# and stop at the earliest match. Each token is matched as a whole
# word (leading space) to avoid e.g. " that " matching inside a noun.
_TITLE_CUT_MARKERS: tuple[str, ...] = (
    " located ",
    " that ",
    " which ",
    " so that ",
    " in order to ",
    " so ",
    " when ",
    " by ",
)


def _derive_title(description: str) -> str:
    """Build a backlog-issue title from the user story description.

    Heuristics (issue #222 — produces short, skimmable backlog titles):

    1. Collapse whitespace; if the text contains "I want ", drop everything
       up to and including the marker (case-insensitive).
    2. Strip a leading article ("a ", "an ", "the ") so the title reads as
       a bare noun phrase.
    3. Cut at the first subordinate-clause opener (``_TITLE_CUT_MARKERS``)
       — these usually introduce restrictive detail that belongs in the
       description, not the title.
    4. Strip the trailing period.
    5. If still over ``_TITLE_SOFT_CAP``, truncate at the last word
       boundary that fits and append "…". A ``_TITLE_HARD_CAP`` fallback
       ensures we never return more than 120 chars even on pathological
       single-word inputs.
    6. Capitalise the first letter so the title reads as a label.
    7. Empty / whitespace-only → ``(untitled RTIA export)``.
    """
    text = (description or "").strip().replace("\n", " ")
    # Collapse internal whitespace so word-boundary math below is honest.
    text = " ".join(text.split())
    lower = text.lower()
    marker = "i want "
    idx = lower.find(marker)
    if idx >= 0:
        text = text[idx + len(marker) :].strip()

    # Drop a leading article so the title is a noun phrase.
    for article in ("a ", "an ", "the "):
        if text.lower().startswith(article):
            text = text[len(article) :]
            break

    # Cut at the first subordinate-clause opener — but only if cutting
    # leaves at least a few words, otherwise the cut produces a useless
    # 1-2 word stub.
    lower = text.lower()
    cut_idx = -1
    for token in _TITLE_CUT_MARKERS:
        candidate = lower.find(token)
        if candidate > 0 and (cut_idx == -1 or candidate < cut_idx):
            cut_idx = candidate
    if cut_idx > 0 and len(text[:cut_idx].split()) >= 2:
        text = text[:cut_idx].rstrip()

    text = text.rstrip(".").strip()

    # Word-boundary soft cap.
    if len(text) > _TITLE_SOFT_CAP:
        truncated = text[:_TITLE_SOFT_CAP].rsplit(" ", 1)[0].rstrip()
        if truncated:
            text = truncated + "…"
    # Pathological single-word case where the soft cap couldn't fire on
    # a word boundary — hard cap with the legacy "..." sentinel.
    if len(text) > _TITLE_HARD_CAP:
        text = text[: _TITLE_HARD_CAP - 3] + "..."

    if text:
        text = text[0].upper() + text[1:]
    return text or "(untitled RTIA export)"


__all__ = ["PipelineRunner"]
