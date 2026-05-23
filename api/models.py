"""Pydantic request/response models for the RTIA API.

The status enum + payload shapes here mirror the LangGraph interrupt
contract (see ``agents/graph.py`` — ``po_checkpoint_node`` and
``story_review_checkpoint_node``). Keeping the API's surface a direct
shape-preserving translation of the graph contract avoids inventing a
parallel vocabulary that would drift.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ThreadStatus(StrEnum):
    """Where the pipeline is for a given thread."""

    RUNNING = "running"
    PAUSED_PO = "paused_po"
    PAUSED_REVIEW = "paused_review"
    DONE = "done"
    ERROR = "error"


class PipelineRequest(BaseModel):
    """Body for ``POST /pipeline``."""

    requirement_text: str = Field(..., min_length=1)


class ResumeRequest(BaseModel):
    """Body for ``POST /pipeline/{thread_id}/resume``.

    Shape depends on which checkpoint the thread is paused at — the
    caller's responsibility to send the right keys (the runner validates
    by attempting to use them; LangGraph raises if the shape is wrong
    and the runner surfaces that as a 400).
    """

    # PO checkpoint: dict[str, str] of {question: answer}.
    answers: dict[str, str] | None = None

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
    "ThreadState",
    "ThreadStatus",
    "UploadResult",
]
