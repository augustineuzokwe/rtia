"""Structured LLM-failure errors (Phase 12.5).

When an LLM call exhausts the retry budget (ADR-0003: 5 retries on
5xx / 429 with exponential backoff), the underlying Gemini exception
propagates out of LangChain. Today's code treats that as an uncaught
exception and the pipeline crashes — the caller gets a stack trace
rather than an artifact.

This module replaces the crash with a structured failure:

1. ``LLMPipelineError`` wraps the underlying exception, attributing it
   to the specific agent that was running and carrying a fixed shape
   that downstream consumers can rely on.
2. ``wrap_llm_exception`` is the conversion helper called inside each
   agent's library function. Maps the Gemini SDK's exception classes
   to the structured form, preserving the HTTP status and message.

The structured form is serialised to JSON and stashed in
``FinalUserStory.metadata['error']`` by ``invoke_pipeline_safely``
(see ``agents/graph.py``), so the artifact returned to the caller
carries the failure information legibly. See
``docs/adr-0009-llm-fallback.md`` for the policy decision and the
alternatives considered (silent model fallback rejected, hard crash
rejected).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

# Imported lazily-friendly: top-level import keeps the helper fast, but
# the Gemini SDK is already a hard dependency of every agent so there is
# no additional install cost.
from google.genai import errors as gemini_errors


@dataclass(frozen=True)
class LLMFailureDetail:
    """JSON-serialisable payload for ``FinalUserStory.metadata['error']``.

    Stable contract: downstream consumers (LangGraph checkpoint UI,
    future API client, eval reports) parse this shape. Adding fields
    is safe; removing or renaming requires a schema bump.
    """

    agent: str
    """Which agent was running when the failure occurred (e.g. 'requirements_analyst')."""
    error_class: str
    """Underlying exception class name (e.g. 'ServerError', 'ClientError', 'TimeoutError')."""
    http_status: int | None
    """HTTP status from the Gemini API response, or None for transport-level failures."""
    message: str
    """Human-readable failure message. Bounded — see _MAX_MESSAGE_CHARS."""
    retries_attempted: int
    """How many retries were attempted before giving up. Pulled from ``agents.config``."""
    occurred_at: str
    """ISO-8601 timestamp (UTC) of when the failure was wrapped."""

    def to_json(self) -> str:
        """Render as a compact JSON string for metadata storage."""
        return json.dumps(asdict(self), separators=(",", ":"))


# Cap the stored message so a chatty server response cannot blow up the
# artifact size (which is also bounded by ``agents/_sanitize.py``'s
# DEFAULT_MAX_CHARS, but defence in depth — bounding here keeps the
# JSON payload predictably small).
_MAX_MESSAGE_CHARS = 500


class PipelineStepError(RuntimeError):
    """Raised by a pipeline node when one of its steps fails.

    Phase 13.4 broadens the original Phase 12.5 contract: any unexpected
    failure inside a pipeline node (Pydantic validation, JSON parse,
    programmer error, transport blip outside the Gemini SDK's catch) is
    converted to ``PipelineStepError`` at the node boundary so the demo
    and any future API caller see *exactly one* exception type. The
    caller builds a stub ``FinalUserStory`` and surfaces the failure in
    ``metadata['error']`` rather than crashing.

    ``LLMPipelineError`` is the narrower subclass for the specific
    "Gemini retry budget exhausted" case carrying HTTP status. Callers
    that want to distinguish can ``isinstance`` it. Callers that only
    care "something failed" catch ``PipelineStepError`` and get both.

    Detail shape: a ``LLMFailureDetail`` with ``http_status=None`` and
    ``retries_attempted=0`` for the non-LLM case. Keeping one detail
    type — rather than introducing a sibling — means the artifact
    metadata schema stays stable for downstream consumers.
    """

    def __init__(self, detail: LLMFailureDetail) -> None:
        self.detail = detail
        super().__init__(self._format_message(detail))

    @staticmethod
    def _format_message(detail: LLMFailureDetail) -> str:
        # For non-LLM failures (http_status=None, retries=0) the
        # "(class=…, status=…, retries=…)" suffix would be noise; emit a
        # cleaner single-line form. LLM failures keep the original
        # diagnostic suffix so 12.5's log shape is preserved.
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

    "Ultimately fails" means: after the SDK's retry budget is exhausted,
    or for non-retryable errors, on the first failure. The caller
    (``invoke_pipeline_safely`` in ``agents/graph.py``) catches this
    exception and produces a stub ``FinalUserStory`` with the structured
    detail JSON-encoded in ``metadata['error']``.

    Never silently fall back to a different model on this exception.
    The whole point of structuring the failure is to surface it clearly
    to the operator — a transparent retry to a different model would
    defeat the policy. See ADR-0009.
    """


def _truncate(text: str, limit: int = _MAX_MESSAGE_CHARS) -> str:
    """Cap message length with a clear trailing marker on truncation."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _classify(exc: BaseException) -> tuple[int | None, str]:
    """Extract (http_status, message) from a Gemini or transport exception.

    Returns ``(None, str(exc))`` for any exception type we don't
    specifically recognise — that keeps the wrapper safe for future
    SDK changes without silently losing failure information.
    """
    if isinstance(exc, gemini_errors.APIError):
        # APIError carries code (int) + message (str). Both attrs are
        # always present per the SDK contract; treat them as the source
        # of truth even if str(exc) would format differently.
        return exc.code, _truncate(str(exc.message or "") or str(exc))
    # TimeoutError / ConnectionError / socket.gaierror / etc. — no
    # HTTP status, just the message.
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
    than inferring from a traceback) keeps the attribution unambiguous
    even if the failing call is wrapped by multiple layers of helpers.

    ``retries_attempted`` is the agent's effective retry count
    (``agents.config.DEFAULT_MAX_RETRIES`` for the standard path,
    overridable by the caller). Reported in the failure detail so an
    operator triaging a 503 storm knows how patient the pipeline was
    being.
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

    Phase 13.4. Sibling of :func:`wrap_llm_exception` for failures that
    are NOT "Gemini retry budget exhausted" — Pydantic validation,
    JSON parse, programmer errors, anything else a node can raise. The
    resulting ``PipelineStepError`` carries the same
    ``LLMFailureDetail`` shape so the demo and the artifact metadata
    schema don't have to branch on the failure source.

    ``http_status`` is ``None`` because there is no API call involved;
    ``retries_attempted`` is ``0`` because non-LLM failures aren't
    retried. Both are honest values, NOT placeholders — downstream
    consumers can rely on "status is None" meaning "this was not an
    API failure".
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
    """Heuristic — is this the kind of exception the SDK would retry?

    Useful for tests and for any caller that wants to know whether a
    given failure is the "the LLM is overloaded" class (which Phase
    12.5's structured-error policy is designed for) vs a programmer
    error like a malformed prompt template.

    The Gemini SDK retries on 5xx and 429. We mirror that classification
    here, plus ``TimeoutError`` and ``ConnectionError`` (transport
    blips) as "the LLM was unreachable" failures.
    """
    if isinstance(exc, gemini_errors.APIError):
        code = getattr(exc, "code", None)
        if code is None:
            return False
        if code == 429:
            return True
        return 500 <= code < 600
    return isinstance(exc, (TimeoutError, ConnectionError))
