"""Shared agent configuration constants.

Single source of truth for cross-agent defaults — primarily the model ID
and resilience knobs. Centralizing here means a model bump or a default
timeout change happens in one place and propagates to every agent, instead
of needing matching edits across agents/*.py.

See `docs/adr-0006-provider-switch.md` for the rationale behind the current
provider/model choice (Gemini 2.5 Flash on Google AI Studio free tier).
ADR-0001 captures the prior Claude Opus 4.7 choice and is now superseded.
"""

from __future__ import annotations

import hashlib

DEFAULT_MODEL = "gemini-2.5-flash"
"""Canonical Gemini model ID used by all production agents.

Stable, free-tier-eligible on Google AI Studio (verified 2026-05-21:
≥10 RPM / 250 RPD quota; selection confirmed against the live
`models.list` endpoint). Bump cautiously — newer Gemini IDs (3.x) are
preview-only at time of writing and their behaviour can shift without
notice. See ADR-0006 for the cutover rationale from Claude Opus 4.7.
"""

DEFAULT_TIMEOUT_SECONDS = 60.0
"""Wall-clock seconds per LLM call. Caps stuck network requests."""

DEFAULT_MAX_RETRIES = 5
"""LangChain retry count for transient errors.

The Gemini wrapper retries on rate limits and transient server errors with
exponential backoff. Patience budget with N=5 is comparable to the
Anthropic-era setting documented in ADR-0003 — tuned so a brief load event
doesn't wedge an interactive demo while a sustained outage fails fast.
Override per call when the agent runs in a different SLO context
(tight: 2; batch: 10+).

NOTE: retries are silent at the wrapper layer. LangSmith trace metadata
remains the place to surface retry counts if/when needed for debugging.
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
