"""Structured LLM-failure errors.

When an LLM call exhausts its retry budget (ADR-0003) the Gemini exception
propagates out of LangChain. This module replaces the resulting pipeline
crash with a structured ``LLMPipelineError`` that wraps the underlying
exception, attributes it to the failing agent, and preserves HTTP status.

The structured form is serialised to JSON and stashed in
``FinalUserStory.metadata['error']`` by ``invoke_pipeline_safely``
(``agents/graph.py``) so the artifact returned to the caller carries the
failure legibly. See ``docs/adr-0009-llm-fallback.md`` for the policy
decision - silent model fallback was rejected.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from google.genai import errors as gemini_errors


@dataclass(frozen=True)
class LLMFailureDetail:
    """JSON-serialisable payload for ``FinalUserStory.metadata['error']``.

    Stable contract: downstream consumers (LangGraph checkpoint UI, API
    clients, eval reports) parse this shape. Adding fields is safe;
    removing or renaming requires a schema bump.
    """

    agent: str
    """Which agent was running when the failure occurred (e.g. 'requirements_analyst')."""
    error_class: str
    """Underlying exception class name (e.g. 'ServerError', 'ClientError', 'TimeoutError')."""
    http_status: int | None
    """HTTP status from the Gemini API response, or None for transport-level failures."""
    message: str
    """Human-readable failure message. Bounded - see _MAX_MESSAGE_CHARS."""
    retries_attempted: int
    """How many retries were attempted before giving up. Pulled from ``agents.config``."""
    occurred_at: str
    """ISO-8601 timestamp (UTC) of when the failure was wrapped."""

    def to_json(self) -> str:
        """Render as a compact JSON string for metadata storage."""
        return json.dumps(asdict(self), separators=(",", ":"))


# Defence-in-depth bound on stored message size. Artifact size is also
# capped by ``agents/_sanitize.py``'s DEFAULT_MAX_CHARS; bounding here
# keeps the JSON payload predictably small.
_MAX_MESSAGE_CHARS = 500


class PipelineStepError(RuntimeError):
    """Raised by a pipeline node when one of its steps fails.

    Any unexpected failure inside a pipeline node (Pydantic validation,
    JSON parse, programmer error, transport blip outside the Gemini SDK)
    is converted to ``PipelineStepError`` at the node boundary so the
    demo and API caller see *exactly one* exception type. The caller
    builds a stub ``FinalUserStory`` and surfaces the failure in
    ``metadata['error']`` rather than crashing.

    ``LLMPipelineError`` is the narrower subclass for the "Gemini retry
    budget exhausted" case carrying HTTP status. Callers that want to
    distinguish can ``isinstance`` it; callers that only care "something
    failed" catch ``PipelineStepError`` and get both.

    Detail shape: ``LLMFailureDetail`` with ``http_status=None`` and
    ``retries_attempted=0`` for the non-LLM case. One detail type keeps
    the artifact metadata schema stable for downstream consumers.
    """

    def __init__(self, detail: LLMFailureDetail) -> None:
        self.detail = detail
        super().__init__(self._format_message(detail))

    @staticmethod
    def _format_message(detail: LLMFailureDetail) -> str:
        # Non-LLM failures (http_status=None, retries=0): cleaner suffix.
        # LLM failures keep the diagnostic suffix to preserve log shape.
        if detail.http_status is None and detail.retries_attempted == 0:
            return (
                f"Pipeline step failed in agent '{detail.agent}' "
                f"(class={detail.error_class}): {detail.message}"
            )
        return (
            f"LLM call failed in agent '{detail.agent}' "
            f"(class={detail.error_class}, status={detail.http_status}, "
            f"retries={detail.retries_attempted}): {detail.message}"
        )


class LLMPipelineError(PipelineStepError):
    """Raised by an agent's library function when its LLM call ultimately fails.

    "Ultimately fails" means: after the SDK retry budget is exhausted, or
    on the first failure for non-retryable errors. The caller
    (``invoke_pipeline_safely`` in ``agents/graph.py``) catches this and
    produces a stub ``FinalUserStory`` with the structured detail
    JSON-encoded in ``metadata['error']``.

    Never silently fall back to a different model on this exception -
    the whole point of structuring the failure is to surface it. A
    transparent retry to a different model would defeat the policy.
    See ADR-0009.
    """


def _truncate(text: str, limit: int = _MAX_MESSAGE_CHARS) -> str:
    """Cap message length with a clear trailing marker on truncation."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _classify(exc: BaseException) -> tuple[int | None, str]:
    """Extract (http_status, message) from a Gemini or transport exception.

    Returns ``(None, str(exc))`` for unrecognised types - keeps the
    wrapper safe for future SDK changes without losing failure info.
    """
    if isinstance(exc, gemini_errors.APIError):
        # APIError contract: code (int) + message (str) always present.
        return exc.code, _truncate(str(exc.message or "") or str(exc))
    # TimeoutError / ConnectionError / socket.gaierror - no HTTP status.
    return None, _truncate(str(exc) or exc.__class__.__name__)


def wrap_llm_exception(
    agent_name: str,
    exc: BaseException,
    *,
    retries_attempted: int,
) -> LLMPipelineError:
    """Convert a raw Gemini/transport exception into a structured ``LLMPipelineError``.

    Called inside each agent's library function from a ``try/except``
    around the LangChain invoke. Pinning ``agent_name`` here (rather
    than inferring from a traceback) keeps attribution unambiguous when
    the failing call sits behind multiple helper layers.

    ``retries_attempted`` is the agent's effective retry count
    (``agents.config.DEFAULT_MAX_RETRIES`` by default). Reported in the
    failure detail so an operator triaging a 503 storm knows how patient
    the pipeline was being.
    """
    http_status, message = _classify(exc)
    detail = LLMFailureDetail(
        agent=agent_name,
        error_class=exc.__class__.__name__,
        http_status=http_status,
        message=message,
        retries_attempted=retries_attempted,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    return LLMPipelineError(detail)


def wrap_step_exception(agent_name: str, exc: BaseException) -> PipelineStepError:
    """Convert any non-LLM exception inside a pipeline node into a structured failure.

    Sibling of :func:`wrap_llm_exception` for failures that are NOT
    "Gemini retry budget exhausted" - Pydantic validation, JSON parse,
    programmer errors. The resulting ``PipelineStepError`` carries the
    same ``LLMFailureDetail`` shape so the artifact metadata schema
    doesn't have to branch on failure source.

    ``http_status=None`` and ``retries_attempted=0`` are honest values,
    NOT placeholders - downstream consumers can rely on "status is None"
    meaning "this was not an API failure".
    """
    detail = LLMFailureDetail(
        agent=agent_name,
        error_class=exc.__class__.__name__,
        http_status=None,
        message=_truncate(str(exc) or exc.__class__.__name__),
        retries_attempted=0,
        occurred_at=datetime.now(UTC).isoformat(),
    )
    return PipelineStepError(detail)


def is_retryable_llm_failure(exc: BaseException) -> bool:
    """Heuristic - is this the kind of exception the SDK would retry?

    Useful for tests and for callers that need to distinguish "LLM is
    overloaded" (the case the structured-error policy is designed for)
    from programmer errors like a malformed prompt template.

    Mirrors Gemini SDK retry classification: 5xx and 429. Plus
    ``TimeoutError`` and ``ConnectionError`` (transport blips) as "the
    LLM was unreachable" failures.
    """
    if isinstance(exc, gemini_errors.APIError):
        code = getattr(exc, "code", None)
        if code is None:
            return False
        if code == 429:
            return True
        return 500 <= code < 600
    return isinstance(exc, (TimeoutError, ConnectionError))
