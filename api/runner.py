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
from agents.graph import build_pipeline, build_stub_artifact_from_error
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
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.DONE,
                payload={
                    "final_artifact": artifact.model_dump(),
                    "rendered_artifact": artifact.as_markdown(),
                    "review_report": review.model_dump() if review is not None else None,
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
        if "critical_ambiguities" in payload:
            return ThreadState(
                thread_id=thread_id,
                status=ThreadStatus.PAUSED_PO,
                payload={"critical_ambiguities": list(payload["critical_ambiguities"])},
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
        artifact = result["final_artifact"]
        review = result.get("review_report")
        return ThreadState(
            thread_id=thread_id,
            status=ThreadStatus.DONE,
            payload={
                "final_artifact": artifact.model_dump(),
                "rendered_artifact": artifact.as_markdown(),
                "review_report": review.model_dump() if review is not None else None,
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


def _derive_title(description: str) -> str:
    """Build a backlog-issue title from the user story description.

    Heuristics:
    - If the description starts with "As a/an <role>, I want <feature>",
      trim to the "I want" clause and cap at 120 chars.
    - Otherwise use the first line, capped.
    - Strip the closing period so the title doesn't end with one.
    """
    text = (description or "").strip().replace("\n", " ")
    lower = text.lower()
    marker = "i want "
    idx = lower.find(marker)
    if idx >= 0:
        text = text[idx + len(marker) :].strip()
    text = text.rstrip(".").strip()
    if len(text) > 120:
        text = text[:117] + "..."
    return text or "(untitled RTIA export)"


__all__ = ["PipelineRunner"]
