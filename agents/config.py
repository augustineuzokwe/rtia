"""Shared agent configuration constants.

Single source of truth for cross-agent defaults — primarily the model ID
and resilience knobs. Centralizing here means a model bump or a default
timeout change happens in one place and propagates to every agent, instead
of needing matching edits across agents/*.py.

See `docs/adr-0001-model-pinning.md` for the rationale behind the model
choice and the version-pinning policy.
"""

from __future__ import annotations

DEFAULT_MODEL = "claude-opus-4-7"
"""Canonical Anthropic model ID for v1.

NOTE: Anthropic does not publish a dated suffix for models 4.6+ — verified
via `client.models.list()` against the API on 2026-05-19. When Anthropic
publishes dated forms (e.g. `claude-opus-4-7-YYYYMMDD`), update this
constant to the dated ID for full reproducibility. See ADR-0001.
"""

DEFAULT_TIMEOUT_SECONDS = 60.0
"""Wall-clock seconds per Claude call. Caps stuck network requests."""

DEFAULT_MAX_RETRIES = 3
"""Retries on transient errors (429, 5xx). Exponential backoff.

NOTE: retries are silent — callers should surface retry counts via logs
or LangSmith traces so latency spikes remain debuggable.
"""
