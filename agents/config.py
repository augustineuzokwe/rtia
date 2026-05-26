"""Shared agent configuration constants.

Single source of truth for cross-agent defaults — primarily the model ID
and resilience knobs. Centralizing here means a model bump or a default
timeout change happens in one place and propagates to every agent, instead
of needing matching edits across agents/*.py.

See `docs/adr-0007-gemini-3-5-flash-switch.md` for the rationale behind the
current Gemini 3.5 Flash choice. ADR-0006 documents the prior 2.5 Flash
era; ADR-0001 captures the original Claude Opus 4.7 choice.
"""

from __future__ import annotations

import hashlib
import os

LLM_PROVIDER_ENV_VAR = "RTIA_LLM_PROVIDER"
"""Env-var name that selects the chat-LLM provider per process.

Accepted values (case-insensitive): ``google`` (default) or ``ollama``.
When unset or anything else, the production Gemini path runs unchanged.

This knob exists to support the local-model probe in §7.3 of the
plan at ``~/.claude/plans/before-we-draft-adr-declarative-leaf.md``
without introducing a provider-abstraction factory. ADR-0006 chose
"one provider, one consumer per import site"; the per-site ``if/else``
branches honour that intent. Promote to a proper factory only when a
third provider lands.
"""

OLLAMA_MODEL_ENV_VAR = "RTIA_OLLAMA_MODEL"
"""Env-var name that picks the Ollama model when the provider is ``ollama``.

Defaults to ``llama3.1:8b`` — the model pulled in §7.1 of the same plan
on a 24 GB M3 MacBook Air. Override at process start to compare other
local models without code edits.
"""

DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


def _llm_provider() -> str:
    return os.environ.get(LLM_PROVIDER_ENV_VAR, "google").strip().lower()


def use_ollama() -> bool:
    """Return True when the process is configured to use the Ollama provider."""
    return _llm_provider() == "ollama"


DEFAULT_MODEL = "gemini-3.5-flash"
"""Canonical Gemini model ID used by all production agents.

Switched from ``gemini-2.5-flash`` on 2026-05-21 after the 2.5-flash
alias hit repeated 503 UNAVAILABLE errors on GitHub-hosted CI runners
(PRs #107, #109). Live probing showed ``gemini-3.5-flash`` routes to
a separate, healthy backend pool — the 503s were not a global Google
outage but a backend-specific congestion on whichever pool the
2.5-flash alias mapped to. See ADR-0007 for the full rationale plus
the live-probe data that motivated the choice.

Still an alias (no dated 3.5-flash suffix exists at switch time).
When Google publishes a dated suffix for the 3.5 line, bump this for
reproducibility — same caveat that applied to 2.5-flash.
"""

DEFAULT_TIMEOUT_SECONDS = 60.0
"""Wall-clock seconds per LLM call. Caps stuck network requests."""

DEFAULT_MAX_RETRIES = 2
"""LangChain retry count for transient errors.

The Gemini wrapper retries on rate limits and transient server errors with
exponential backoff. Trimmed 5→2 in issue #163 (pipeline speedup) — the
prior N=5 stacked badly with the workflow-level retry: a stuck call would
burn 5 SDK attempts × exponential backoff, then ``nick-fields/retry@v4``
(``.github/workflows/ci.yml``) would re-run the entire eval, for up to 10
logical attempts at a single LLM call. With N=2 the layered worst case is
2 SDK × 2 workflow = 4 attempts — still enough to ride out a single 503,
but tail latency is bounded.

Override per call when the agent runs in a different SLO context
(interactive UI may want 1; offline batch may want 5+).

NOTE: retries are silent at the wrapper layer. LangSmith trace metadata
remains the place to surface retry counts if/when needed for debugging.
"""

# Per-agent output-token ceilings (issue #163). Setting an explicit cap
# is the difference between Gemini failing fast on a malformed/over-long
# response vs. spending the full timeout window streaming tokens. Values
# are calibrated at ~2× the maximum output_tokens observed across the 7
# eval samples in ``evals/reports/baseline-2026-05-24.json``, rounded up
# to the nearest 500. Generous enough not to truncate legitimate outputs,
# tight enough to bound tail latency. Re-run ``evals/run_evals.py`` and
# bump these if a future prompt change shifts the distribution.
#
# Reviewer is not in the eval suite (only the LangGraph deep-flow runs
# it). Its cap is set to match Story Writer pending a follow-up
# calibration via ``scripts/run_pipeline_demo.py``.
MAX_OUTPUT_TOKENS_ANALYST = 4000  # observed max 1972 (sample-03)
MAX_OUTPUT_TOKENS_STORY_WRITER = 3000  # observed max 1378 (sample-02)
MAX_OUTPUT_TOKENS_AC_GENERATOR = 4500  # observed max 2042 (sample-06)
MAX_OUTPUT_TOKENS_TEST_CASE_WRITER = 6500  # observed max 3012 (sample-05)
MAX_OUTPUT_TOKENS_REVIEWER = 3000  # uncalibrated — match Story Writer


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
