"""Pydantic request/response models for the RTIA API.

The status enum + payload shapes here mirror the LangGraph interrupt
contract (see ``agents/graph.py`` - ``po_checkpoint_node`` and
``story_review_checkpoint_node``). Keeping the API's surface a direct
shape-preserving translation of the graph contract avoids inventing a
parallel vocabulary that would drift.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ThreadStatus(StrEnum):
    """Where the pipeline is for a given thread.

    ``DONE_SPLIT`` (Phase 15.4) is the terminal status for multi-story
    requirements that branched into the split path instead of the
    deep flow. The payload carries ``split_stories`` (a list of
    lightweight placeholder stories) instead of ``final_artifact``.
    """

    RUNNING = "running"
    PAUSED_PO = "paused_po"
    PAUSED_REVIEW = "paused_review"
    DONE = "done"
    DONE_SPLIT = "done_split"
    ERROR = "error"


class PipelineRequest(BaseModel):
    """Body for ``POST /pipeline``."""

    requirement_text: str = Field(..., min_length=1)

    @field_validator("requirement_text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """Reject whitespace-only input. Pydantic's ``min_length=1`` accepts
        ``"   "`` because it counts characters before strip. The UI already
        guards this client-side (``ui/gradio_app.py:on_run``) but a direct
        API caller could otherwise start an empty pipeline run that the
        Analyst then errors on confusingly. Defense in depth - Epic #1
        intake-soundness audit (#1)."""
        if not value or not value.strip():
            raise ValueError("requirement_text must not be empty or whitespace-only")
        return value


class SelectedStory(BaseModel):
    """One implied-story selection in a split PO resume body (Phase 15.4 / #207).

    The PO checkpoint UI shows each implied story as a Checkbox + editable
    Textbox; on Submit the kept (checked) rows are serialised as
    ``SelectedStory`` items here, with the edited ``title`` and the
    original Analyst-provided ``summary`` preserved.

    ``original_title`` is the Analyst's pre-edit title - used by the
    graph to map the edited title back to the matching implied-story
    summary even when the PO renamed it.
    """

    title: str
    summary: str = ""
    original_title: str = ""


class ResumeRequest(BaseModel):
    """Body for ``POST /pipeline/{thread_id}/resume``.

    Shape depends on which checkpoint the thread is paused at:

    - PO checkpoint, **deep mode**: ``answers`` is a ``dict[question, answer]``.
    - PO checkpoint, **split mode** (Phase 15.4): preferred shape is
      ``selected_stories`` - a list of :class:`SelectedStory` carrying
      possibly-edited titles + matching summaries. Empty / missing
      ``selected_stories`` means "fan out every identified story" (Q2 default).
      The legacy ``selected_story_titles`` (list of plain title strings)
      is still accepted for back-compat; if both are present,
      ``selected_stories`` wins.
    - Story review checkpoint: ``accepted`` ± ``description`` / ``objective``.
    """

    # PO checkpoint, deep mode (and non-story Qs in split mode).
    answers: dict[str, str] | None = None

    # PO checkpoint, split mode - preferred shape (#207).
    selected_stories: list[SelectedStory] | None = None
    # PO checkpoint, split mode - legacy shape (Phase 15.4). Deprecated
    # but accepted; superseded by ``selected_stories`` when both present.
    selected_story_titles: list[str] | None = None

    # Story review checkpoint.
    accepted: bool | None = None
    description: str | None = None
    objective: str | None = None


class ThreadState(BaseModel):
    """Response for ``POST /pipeline``, ``POST /resume``, ``GET /pipeline/{id}``."""

    thread_id: str
    status: ThreadStatus
    payload: dict[str, Any] = Field(default_factory=dict)


class UploadResult(BaseModel):
    """Response for ``POST /uploads/{pdf,markdown}``."""

    text: str
    char_count: int


class ErrorResponse(BaseModel):
    """JSON shape for 4xx/5xx structured errors."""

    error: str
    detail: dict[str, Any] | None = None


__all__ = [
    "ErrorResponse",
    "PipelineRequest",
    "ResumeRequest",
    "SelectedStory",
    "ThreadState",
    "ThreadStatus",
    "UploadResult",
]
