"""Shared agent configuration constants.

Single source of truth for cross-agent defaults — primarily the model ID
and resilience knobs. Centralizing here means a model bump or a default
timeout change happens in one place and propagates to every agent, instead
of needing matching edits across agents/*.py.

See `docs/adr-0001-model-pinning.md` for the rationale behind the model
choice and the version-pinning policy.
"""

from __future__ import annotations

import hashlib

DEFAULT_MODEL = "claude-opus-4-7"
"""Canonical Anthropic model ID for v1.

NOTE: Anthropic does not publish a dated suffix for models 4.6+ — verified
via `client.models.list()` against the API on 2026-05-19. When Anthropic
publishes dated forms (e.g. `claude-opus-4-7-YYYYMMDD`), update this
constant to the dated ID for full reproducibility. See ADR-0001.
"""

DEFAULT_TIMEOUT_SECONDS = 60.0
"""Wall-clock seconds per Claude call. Caps stuck network requests."""

DEFAULT_MAX_RETRIES = 5
"""Anthropic SDK retry count. See `docs/adr-0003-llm-resilience.md`.

What the SDK retries (verified from `anthropic._base_client._should_retry`):
    408 Request Timeout
    409 Lock Timeout
    429 Rate Limit
    >= 500 Server Errors (includes 529 Overloaded that Opus 4.7 emits)

Backoff: exponential `min(0.5 * 2^n, 8.0)` seconds with jitter; respects
`Retry-After` header when ≤ 60s.

Patience budget with N=5: ~0.5+1+2+4+8 = ~15.5s of total wait. Tuned so
a brief Anthropic load event doesn't wedge an interactive demo, while a
sustained outage fails fast. Override per call when the agent runs in a
different SLO context (tight: 2; batch: 10+).

NOTE: retries are silent at the SDK layer. LangSmith trace metadata
exposes `x-stainless-retry-count` so latency spikes remain debuggable.
"""


def prompt_hash(*prompts: str) -> str:
    """Return a short, stable identifier for a set of prompt strings.

    Used as `prompt_hash` in LangSmith trace metadata so every traced
    LLM call can be attributed to the exact prompt version that produced
    it. Editing any prompt content (system or user-template) changes the
    hash; reordering arguments also changes it (deliberate — both
    prompts contribute to model behavior).

    Returns the first 12 hex chars of sha256, which is plenty to avoid
    collisions in a single project's lifetime and short enough to read
    in a trace UI. Truncation is deterministic so the same prompts
    always produce the same value.
    """
    h = hashlib.sha256()
    for prompt in prompts:
        h.update(prompt.encode("utf-8"))
        h.update(b"\x00")  # delimiter so ("ab", "c") != ("a", "bc")
    return h.hexdigest()[:12]
