"""LangSmith observability helpers + production tracing guard.

LangChain auto-instruments LLM calls when LangSmith environment variables
are set - no agent code changes are required. This module exists for two
reasons:

1. Let callers (the demo, future UI, future eval harness) detect whether
   tracing is on and surface a useful status message to the user.
2. Enforce the Phase 12.4 policy: **no LangSmith tracing in production**.
   See ``docs/adr-0008-pii-langsmith.md`` for the full rationale.

Detection lives here (not inline) so the env-var contract is documented
in one place and can be reused by every entry point.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# Allowed values for the RTIA_ENV environment variable. Keep the set
# small - an open string here invites typos that silently fall back to
# the most-permissive default. See ADR-0008.
RtiaEnv = Literal["development", "ci", "production"]
_ALLOWED_ENVS: frozenset[str] = frozenset({"development", "ci", "production"})
_DEFAULT_ENV: RtiaEnv = "development"


class ProductionTracingError(RuntimeError):
    """Raised when ``RTIA_ENV=production`` and LangSmith tracing is enabled.

    A production deployment must NEVER persist requirement text (which
    may contain customer PII) to LangSmith. The pipeline refuses to
    start rather than silently overriding a misconfiguration, because
    silent override hides the underlying config drift from the operator.

    See ``docs/adr-0008-pii-langsmith.md`` for the policy decision and
    the alternatives considered.
    """


def _current_env() -> RtiaEnv:
    """Return the validated ``RTIA_ENV`` value, defaulting to development.

    An unrecognised value (typo, empty string, unset) is treated as
    ``development``. This is the safest default for the assertion path:
    a typo'd ``RTIA_ENV=produciton`` must NOT count as production for
    permissioning purposes - the operator's misconfiguration there
    surfaces elsewhere (the deployment fails for other reasons), and
    granting the more-permissive development behaviour is harmless.
    """
    raw = (os.environ.get("RTIA_ENV") or "").strip().lower()
    if raw in _ALLOWED_ENVS:
        return raw  # type: ignore[return-value]
    return _DEFAULT_ENV


@dataclass(frozen=True)
class TracingStatus:
    """Snapshot of LangSmith tracing configuration at a point in time."""

    enabled: bool
    project: str | None
    reason: str  # human-readable explanation of why it's enabled or not
    env: RtiaEnv = _DEFAULT_ENV
    """RTIA_ENV at the time of the snapshot. Exposed so callers can
    show ``development``/``ci``/``production`` in the status banner
    without re-reading the env var themselves."""

    def status_line(self) -> str:
        """One-line status suitable for printing in a CLI banner."""
        env_tag = f" [env: {self.env}]"
        if self.enabled:
            return f"LangSmith tracing: ON (project: {self.project}){env_tag}"
        return f"LangSmith tracing: OFF ({self.reason}){env_tag}"


def tracing_status() -> TracingStatus:
    """Inspect the current process environment for LangSmith config.

    Tracing is considered ON when ALL of the following hold:

    - ``RTIA_ENV`` is NOT ``production``. Phase 12.4 forces tracing off
      in production regardless of the LangSmith vars - see ADR-0008.
    - ``LANGSMITH_TRACING`` is truthy.
    - ``LANGSMITH_API_KEY`` is present and non-empty.

    Any other combination is treated as off, with a human-readable
    reason so the user can fix whichever half they forgot.
    """
    env = _current_env()
    tracing_flag = (os.environ.get("LANGSMITH_TRACING") or "").strip().lower()
    api_key = (os.environ.get("LANGSMITH_API_KEY") or "").strip()
    project = (os.environ.get("LANGSMITH_PROJECT") or "").strip() or None
    truthy = tracing_flag in {"true", "1", "yes", "on"}

    # Phase 12.4 production guard. Even if the caller hasn't invoked
    # ``assert_safe_for_env`` (defence in depth), every read of the
    # tracing status returns OFF when env=production. Any code that
    # conditions on `enabled` sees the right answer.
    if env == "production":
        return TracingStatus(
            enabled=False,
            project=project,
            reason="forced off by RTIA_ENV=production (see ADR-0008)",
            env=env,
        )

    if truthy and api_key:
        return TracingStatus(enabled=True, project=project, reason="env vars set", env=env)
    if truthy and not api_key:
        return TracingStatus(
            enabled=False,
            project=project,
            reason="LANGSMITH_TRACING=true but LANGSMITH_API_KEY is empty",
            env=env,
        )
    if not truthy and api_key:
        return TracingStatus(
            enabled=False,
            project=project,
            reason="LANGSMITH_API_KEY is set but LANGSMITH_TRACING is not 'true'",
            env=env,
        )
    return TracingStatus(enabled=False, project=project, reason="env vars not set", env=env)


def assert_safe_for_env() -> None:
    """Refuse to start when production is configured with tracing on.

    Called by every process entry point (demo script, future API
    handler) BEFORE any LLM call. Raises ``ProductionTracingError``
    if both ``RTIA_ENV=production`` and ``LANGSMITH_TRACING`` is
    truthy - the misconfiguration that would otherwise exfiltrate
    customer requirement text to LangSmith.

    Per ADR-0008, this is a hard assert rather than a soft override:
    silent override hides the underlying config drift from the
    operator, and the same misconfig then propagates to other
    environments without anyone noticing. Refusing to start is loud
    feedback.

    The check intentionally only inspects ``LANGSMITH_TRACING`` (not
    the full ``tracing_status().enabled``) so an incomplete config
    - e.g. ``LANGSMITH_TRACING=true`` with no API key - still fails
    loudly in production. The operator's *intent* to trace is the
    misconfiguration we are catching, not the *successful* trace.
    """
    env = _current_env()
    if env != "production":
        return
    tracing_flag = (os.environ.get("LANGSMITH_TRACING") or "").strip().lower()
    if tracing_flag in {"true", "1", "yes", "on"}:
        raise ProductionTracingError(
            "RTIA refuses to start: RTIA_ENV=production is incompatible "
            "with LANGSMITH_TRACING=true. Production must not persist "
            "requirement text (potentially containing customer PII) to "
            "LangSmith. Set LANGSMITH_TRACING=false (or unset) before "
            "starting in production. See docs/adr-0008-pii-langsmith.md."
        )
