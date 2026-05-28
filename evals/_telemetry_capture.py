"""Capture per-agent telemetry by listening to the logging stream.

the eval runner needs token counts + wall-clock duration per
agent so the budget gate can enforce cost and latency ceilings. The
per-agent token data exists today only on the LangChain response inside
each agent function; the runner doesn't see it. Restructuring every
``write_user_story``/``generate_acceptance_criteria``/etc. entry point to
return telemetry alongside the parsed output would touch every agent and
every test.

Instead this module installs a stdlib ``logging.Handler`` that listens
on the ``rtia.agents.*`` namespace - exactly where the
``log_agent_invocation`` context manager already emits a structured
``agent_invocation_end`` record for every LLM call. The handler parses
those records back into typed observations. Zero changes to agent code.

Constraints kept honest:

* The handler captures ONLY records emitted while it is attached; it is
  not retroactive and does not survive its own ``__exit__``. Tests stay
  silent because they don't enter this context manager.
* Token counts are pulled from the structured fields the formatter
  already serialises (``input_tokens``, ``output_tokens``,
  ``total_tokens``). Missing fields stay ``None`` - observability code
  must not lie about counts.
* Judge calls are NOT captured here. The judge uses its own
  ``ChatGoogleGenerativeAI`` outside ``log_agent_invocation`` (different
  failure semantics). The eval report names this gap explicitly in the
  ``usage`` aggregate.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from agents._logging import LOGGER_NAMESPACE


@dataclass(frozen=True)
class AgentInvocationObservation:
    """One captured ``agent_invocation_end`` record."""

    agent: str
    status: str  # 'ok' or 'error'
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass
class TelemetryCapture:
    """Bag of observations a caller can inspect after the context exits."""

    observations: list[AgentInvocationObservation] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> int:
        return sum(o.input_tokens or 0 for o in self.observations)

    @property
    def total_output_tokens(self) -> int:
        return sum(o.output_tokens or 0 for o in self.observations)

    @property
    def total_tokens(self) -> int:
        # Prefer the LLM's authoritative total when present; fall back to
        # input+output sum so a provider that doesn't report a total
        # still gives us something.
        explicit = sum(o.total_tokens for o in self.observations if o.total_tokens is not None)
        derived = sum(
            (o.input_tokens or 0) + (o.output_tokens or 0)
            for o in self.observations
            if o.total_tokens is None
        )
        return explicit + derived

    @property
    def total_duration_ms(self) -> int:
        return sum(o.duration_ms for o in self.observations)

    def for_agent(self, name: str) -> AgentInvocationObservation | None:
        """Return the first observation for the given agent, or None."""
        for obs in self.observations:
            if obs.agent == name:
                return obs
        return None


class _CaptureHandler(logging.Handler):
    """Stdlib handler that filters for ``agent_invocation_end`` records.

    Reads structured fields directly off the LogRecord (the formatter
    promotes ``extra={...}`` to attributes), so it doesn't depend on
    the JSON formatter being installed. That decoupling matters: if a
    future caller swaps to a plain-text formatter, capture still works.
    """

    def __init__(self, capture: TelemetryCapture):
        super().__init__(level=logging.DEBUG)
        self._capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "agent_invocation_end":
            return
        agent = getattr(record, "agent", None)
        status = getattr(record, "status", None)
        duration_ms = getattr(record, "duration_ms", None)
        if not isinstance(agent, str) or not isinstance(status, str):
            return
        if not isinstance(duration_ms, int):
            return
        self._capture.observations.append(
            AgentInvocationObservation(
                agent=agent,
                status=status,
                duration_ms=duration_ms,
                input_tokens=_maybe_int(getattr(record, "input_tokens", None)),
                output_tokens=_maybe_int(getattr(record, "output_tokens", None)),
                total_tokens=_maybe_int(getattr(record, "total_tokens", None)),
            )
        )


def _maybe_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


@contextmanager
def capture_agent_telemetry() -> Iterator[TelemetryCapture]:
    """Capture every ``agent_invocation_end`` record emitted in this block.

    Attaches a private handler to the ``rtia`` logger and detaches it on
    exit. Idempotent across nested usage (each enter gets its own
    handler + capture bag). Importantly, the handler is installed at
    ``logging.DEBUG`` even though ``configure_logging`` sets the logger
    at INFO by default - the runner doesn't need to know whether the
    caller bumped the level. The handler accepts everything; the filter
    on ``event == 'agent_invocation_end'`` selects the records we want.
    """
    capture = TelemetryCapture()
    handler = _CaptureHandler(capture)
    logger = logging.getLogger(LOGGER_NAMESPACE)
    # Force the logger's effective level low enough to actually deliver
    # DEBUG records to handlers. We restore the prior level on exit so
    # we don't bleed into other test fixtures or callers.
    prior_level = logger.level
    if prior_level == logging.NOTSET or prior_level > logging.DEBUG:
        logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield capture
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prior_level)
