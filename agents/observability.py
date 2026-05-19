"""LangSmith observability helpers.

LangChain auto-instruments LLM calls when LangSmith environment variables
are set — no agent code changes are required. This module only exists to
let callers (the demo, future UI, future eval harness) detect whether
tracing is on and surface a useful status message to the user.

Detection lives here (not inline) so the env-var contract is documented
in one place and can be reused by every entry point.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TracingStatus:
    """Snapshot of LangSmith tracing configuration at a point in time."""

    enabled: bool
    project: str | None
    reason: str  # human-readable explanation of why it's enabled or not

    def status_line(self) -> str:
        """One-line status suitable for printing in a CLI banner."""
        if self.enabled:
            return f"LangSmith tracing: ON (project: {self.project})"
        return f"LangSmith tracing: OFF ({self.reason})"


def tracing_status() -> TracingStatus:
    """Inspect the current process environment for LangSmith config.

    Tracing is considered ON when both LANGSMITH_TRACING is truthy AND
    LANGSMITH_API_KEY is present. Either alone is treated as off — and
    the reason is reported so a user who forgot one half can fix it.
    """
    tracing_flag = (os.environ.get("LANGSMITH_TRACING") or "").strip().lower()
    api_key = (os.environ.get("LANGSMITH_API_KEY") or "").strip()
    project = (os.environ.get("LANGSMITH_PROJECT") or "").strip() or None

    truthy = tracing_flag in {"true", "1", "yes", "on"}
    if truthy and api_key:
        return TracingStatus(enabled=True, project=project, reason="env vars set")
    if truthy and not api_key:
        return TracingStatus(
            enabled=False,
            project=project,
            reason="LANGSMITH_TRACING=true but LANGSMITH_API_KEY is empty",
        )
    if not truthy and api_key:
        return TracingStatus(
            enabled=False,
            project=project,
            reason="LANGSMITH_API_KEY is set but LANGSMITH_TRACING is not 'true'",
        )
    return TracingStatus(enabled=False, project=project, reason="env vars not set")
