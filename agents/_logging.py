"""Structured JSON logging for RTIA.

This module is intentionally separate from ``agents.observability`` (which
owns the LangSmith integration). LangSmith is a *trace* sink - it can be
disabled (Phase 12.4 forces it off in production) and it can be down.
Structured logs are RTIA's own narrative of what happened: who ran, how
long it took, what it cost, and whether it failed. They survive a
LangSmith outage and they survive ``RTIA_ENV=production`` (which
suppresses traces, never logs).

Design choices worth knowing about:

* **Destination defaults to stderr**, not stdout. The plan asked for
  "to stdout", but RTIA's primary entry point (``scripts/run_pipeline_demo.py``)
  prints the rendered artifact to stdout for the user to paste into Jira.
  Interleaving JSON log lines with that markdown would break that contract.
  Override via ``RTIA_LOG_DESTINATION=stdout`` if a downstream consumer
  pipes the entire process output to a log shipper.
* **No automatic configuration on import.** Library code should never
  configure root logging behind the caller's back. Entry points
  (``run_pipeline_demo``, future API handler) call
  :func:`configure_logging` explicitly. Tests stay silent because they
  don't call it.
* **One JSON object per line** so the logs are grep-able with ``jq`` /
  ``rg`` and ingestible by any log shipper without a custom parser.
* **Token usage is best-effort.** Different LangChain providers expose
  ``usage_metadata`` with slightly different shapes; the extractor falls
  back gracefully when a field is missing rather than raising and
  corrupting the success path. The point of these logs is observability,
  not correctness - a missing token count must never break the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import IO, Any

LOGGER_NAMESPACE = "rtia"
"""Top-level logger name. All RTIA loggers are children of this (e.g.
``rtia.agents.requirements_analyst``). Library code attaches a NullHandler
here so unconfigured callers (tests) stay silent without the
``No handlers could be found`` warning."""

_DEFAULT_LEVEL = "INFO"
_DEFAULT_DESTINATION = "stderr"

# Module-level flag guarding ``configure_logging``. Repeat calls are
# idempotent - handy when both the demo's main() and a unit test
# (which should never trigger it) end up importing the same module.
_configured: bool = False


def _resolve_level() -> int:
    """Read RTIA_LOG_LEVEL from env. Unknown values fall back to INFO.

    We intentionally do NOT raise on a typo - an operator setting
    ``RTIA_LOG_LEVEL=INF0`` (zero, not O) at 3 AM should still get logs,
    just at the default level. The fallback is logged at WARNING once
    so the misconfiguration is visible.
    """
    raw = (os.environ.get("RTIA_LOG_LEVEL") or _DEFAULT_LEVEL).strip().upper()
    level = logging.getLevelNamesMapping().get(raw)
    if level is None:
        logging.getLogger(LOGGER_NAMESPACE).warning(
            "Unknown RTIA_LOG_LEVEL=%r; falling back to %s", raw, _DEFAULT_LEVEL
        )
        return logging.INFO
    return level


def _resolve_destination() -> IO[str]:
    """Pick the output stream per RTIA_LOG_DESTINATION."""
    raw = (os.environ.get("RTIA_LOG_DESTINATION") or _DEFAULT_DESTINATION).strip().lower()
    if raw == "stdout":
        return sys.stdout
    return sys.stderr


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    The schema is intentionally flat:

    * ``ts`` - UNIX epoch milliseconds (integer). Easier to sort than ISO.
    * ``level`` - WARNING / INFO / DEBUG / ERROR.
    * ``logger`` - full dotted logger name (``rtia.agents.foo``).
    * ``event`` - short kebab-case event name from ``extra={"event": ...}``.
    * ``message`` - the human-readable message (often empty for structured
      events; useful when grepping).
    * Any extra fields from ``extra={...}`` are merged at the top level.

    A nested ``context`` namespace was considered and rejected - flat
    is much easier to query with ``jq '.duration_ms'`` than
    ``jq '.context.duration_ms'``.
    """

    _RESERVED: frozenset[str] = frozenset(
        # Standard LogRecord attributes we never want to forward as fields.
        # Anything the caller passes via extra= that doesn't collide ends up
        # in the JSON line.
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "taskName",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Promote any caller-supplied extras to top-level fields.
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # ``default=str`` makes the formatter robust to surprise types
        # (Pydantic models, datetimes) - observability code must never
        # crash the caller because someone passed a fancy object.
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, force: bool = False) -> None:
    """Install the JSON handler on the ``rtia`` logger namespace.

    Idempotent: repeat calls are no-ops unless ``force=True``. Tests can
    use ``force=True`` to swap handlers between cases.

    Only touches the ``rtia`` logger - never the root logger, never the
    handlers of other libraries. This is what makes the module safe to
    import from anywhere.
    """
    global _configured
    if _configured and not force:
        return

    logger = logging.getLogger(LOGGER_NAMESPACE)
    # Remove any previous handlers (so a force-reconfigure doesn't leak
    # duplicates) but only the ones WE installed. We tag them via the
    # ``_rtia`` attribute to distinguish from any handler a caller may
    # have attached themselves.
    for handler in list(logger.handlers):
        if getattr(handler, "_rtia", False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler(_resolve_destination())
    handler.setFormatter(JsonFormatter())
    handler._rtia = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(_resolve_level())
    # Stop propagation so the same record doesn't also hit the root
    # logger (which a downstream caller may have configured for plain
    # text). Our handler is the single source of truth for rtia.* logs.
    logger.propagate = False

    _configured = True


# Attach a NullHandler at module import so unconfigured callers (tests,
# library users who never call configure_logging) don't see
# ``No handlers could be found`` warnings. Python's stdlib pattern.
logging.getLogger(LOGGER_NAMESPACE).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``rtia`` namespace.

    Pass ``__name__`` from agent modules. Prefix is automatically
    normalised so ``agents.requirements_analyst`` becomes
    ``rtia.agents.requirements_analyst``.
    """
    if name.startswith(LOGGER_NAMESPACE + ".") or name == LOGGER_NAMESPACE:
        full = name
    else:
        full = f"{LOGGER_NAMESPACE}.{name}"
    return logging.getLogger(full)


def _extract_token_usage(response: Any) -> dict[str, int | None]:
    """Best-effort pull of input/output/total token counts from a LangChain response.

    Gemini via ``langchain-google-genai`` exposes
    ``response.usage_metadata = {'input_tokens': N, 'output_tokens': N,
    'total_tokens': N}`` (the LangChain v0.3+ standard). Older
    providers used ``response.response_metadata['token_usage']`` with
    different keys. We check both so the helper survives a provider swap.
    Missing → ``None`` (not zero, because zero is a valid count and we
    must not lie about it).
    """
    out: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        for key in out:
            value = usage.get(key)
            if isinstance(value, int):
                out[key] = value
        return out

    # Fallback: ``response.response_metadata['token_usage']`` shape.
    meta = getattr(response, "response_metadata", None)
    if isinstance(meta, dict):
        tu = meta.get("token_usage")
        if isinstance(tu, dict):
            mapping = {
                "input_tokens": ("prompt_tokens", "input_tokens"),
                "output_tokens": ("completion_tokens", "output_tokens"),
                "total_tokens": ("total_tokens",),
            }
            for canonical, candidates in mapping.items():
                for cand in candidates:
                    value = tu.get(cand)
                    if isinstance(value, int):
                        out[canonical] = value
                        break
    return out


@dataclass
class InvocationRecord:
    """Mutable handle the agent code uses to attach observed metadata.

    Returned by :func:`log_agent_invocation`. The agent calls
    :meth:`record_response` after a successful LLM call so token usage
    can be pulled from the LangChain response. The context manager itself
    times the block and emits the final log record.
    """

    agent: str
    start: float
    tokens: dict[str, int | None] = field(
        default_factory=lambda: {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    )

    def record_response(self, response: Any) -> None:
        """Pull token counts from a LangChain response object."""
        self.tokens = _extract_token_usage(response)


@contextmanager
def log_agent_invocation(
    agent: str, *, prompt_hash: str | None = None
) -> Iterator[InvocationRecord]:
    """Time and log a single agent LLM invocation.

    Emits two records:

    * ``event=agent_invocation_start`` (DEBUG) when the block enters.
      Useful when chasing hangs; suppressed at default INFO level.
    * ``event=agent_invocation_end`` (INFO on success, ERROR on
      exception) when the block exits. Includes ``duration_ms``,
      ``status``, token counts, and (on exception) the error class and
      message - but NOT the requirement text. Per ADR-0008, logs must
      not exfiltrate customer input.

    Usage::

        with log_agent_invocation("requirements_analyst",
                                  prompt_hash=_PROMPT_HASH) as rec:
            response = llm.invoke(messages, config=config)
            rec.record_response(response)

    The ``raise`` path is automatic - an exception inside the block is
    logged with status=error and re-raised unchanged.
    """
    logger = get_logger(f"agents.{agent}")
    start = time.monotonic()
    record = InvocationRecord(agent=agent, start=start)
    base_fields: dict[str, Any] = {"event": "agent_invocation_start", "agent": agent}
    if prompt_hash:
        base_fields["prompt_hash"] = prompt_hash
    logger.debug("agent invocation start", extra=base_fields)
    try:
        yield record
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "agent invocation failed",
            extra={
                "event": "agent_invocation_end",
                "agent": agent,
                "status": "error",
                "duration_ms": duration_ms,
                "error_class": type(exc).__name__,
                # ``str(exc)`` not ``repr(exc)``: we want the human
                # message, not the constructor call. Repr can include
                # quoted prompt fragments on some SDK exceptions.
                "error_message": str(exc),
                **({"prompt_hash": prompt_hash} if prompt_hash else {}),
            },
        )
        raise
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "agent invocation ok",
            extra={
                "event": "agent_invocation_end",
                "agent": agent,
                "status": "ok",
                "duration_ms": duration_ms,
                **record.tokens,
                **({"prompt_hash": prompt_hash} if prompt_hash else {}),
            },
        )


__all__ = [
    "InvocationRecord",
    "JsonFormatter",
    "LOGGER_NAMESPACE",
    "configure_logging",
    "get_logger",
    "log_agent_invocation",
]
