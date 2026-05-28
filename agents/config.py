"""Shared agent configuration constants.

Single source of truth for cross-agent defaults (model ID, timeouts,
retries, token caps). See ADR-0001/0006/0007 for the provider history.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

LLM_PROVIDER_ENV_VAR = "RTIA_LLM_PROVIDER"
"""Selects the chat-LLM provider. Values: ``google`` (default) or ``ollama``.

Supports the local-model probe in ``docs/ollama-probe-2026-05-26.md``
without introducing a provider-abstraction factory. ADR-0006 chose
"one provider, one consumer per import site"; promote to a real factory
only when a third provider lands.
"""

OLLAMA_MODEL_ENV_VAR = "RTIA_OLLAMA_MODEL"
"""Picks the Ollama model when the provider is ``ollama``. Default
``llama3.1:8b``. Override to compare other local models without code edits.
"""

DEFAULT_OLLAMA_MODEL = "llama3.1:8b"

OLLAMA_JUDGE_ENV_VAR = "RTIA_OLLAMA_JUDGE"
"""Routes the deepeval judge through Ollama for a full-local stack.

Truthy values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
Default keeps the judge on Gemini so the generator swap can be measured
apples-to-apples against ``docs/pipeline-baseline-2026-05-26.md``. Set
this to ``1`` only when answering "is RTIA usable end-to-end without
any API spend?" rather than measuring generator degradation alone.
"""

OLLAMA_JUDGE_MODEL_ENV_VAR = "RTIA_OLLAMA_JUDGE_MODEL"
"""Picks the Ollama judge model when ``RTIA_OLLAMA_JUDGE`` is on.
Defaults to ``DEFAULT_OLLAMA_MODEL``. Override for an asymmetric stack
(e.g. 8B generator + 14B judge to recover precision).
"""

_OLLAMA_JUDGE_TRUTHY = {"1", "true", "yes", "on"}


def _llm_provider() -> str:
    return os.environ.get(LLM_PROVIDER_ENV_VAR, "google").strip().lower()


def use_ollama() -> bool:
    """Return True when the process is configured to use the Ollama provider."""
    return _llm_provider() == "ollama"


def use_ollama_judge() -> bool:
    """Return True when the deepeval judge should run through Ollama.

    Independent of :func:`use_ollama` - generator and judge have separate
    switches so the judge default stays on Gemini regardless of generator.
    """
    raw = os.environ.get(OLLAMA_JUDGE_ENV_VAR, "").strip().lower()
    return raw in _OLLAMA_JUDGE_TRUTHY


LLM_CACHE_ENABLED_ENV_VAR = "RTIA_LLM_CACHE"
"""Enables/disables the LLM response cache. Values: ``enabled`` (default)
or ``disabled``.

CI sets ``disabled`` so the eval gate re-measures live behaviour every PR -
see [Issue #230](https://github.com/augustineuzokwe/rtia/issues/230) for
the "false-green CI" trap this avoids. Cache key includes ``prompt_hash``
so prompt edits auto-invalidate.
"""

LLM_CACHE_TTL_ENV_VAR = "RTIA_LLM_CACHE_TTL"
"""Cache TTL in seconds. Default 86 400 (24h).

Shorter than Promptfoo's 14-day default - RTIA iterates fast enough that
a 14-day stale window would hide real model drift. 24h bounds the
worst-case stale window to one workday.
"""

LLM_CACHE_DIR_ENV_VAR = "RTIA_LLM_CACHE_DIR"
"""Cache directory. Default ``~/.rtia/cache/`` (mirrors ``~/.rtia/state.db``
from ADR-0002). Override only when sharing a cache between worktrees.
"""

DEFAULT_CACHE_DIR = Path("~/.rtia/cache").expanduser()
DEFAULT_CACHE_TTL_SECONDS = 86400  # 24 hours

DEFAULT_MODEL = "gemini-3.5-flash"
"""Canonical Gemini model used by all production agents.

Switched from ``gemini-2.5-flash`` on 2026-05-21 after the 2.5-flash
alias hit repeated 503s on GitHub-hosted CI runners (PRs #107, #109).
Live probing showed 3.5-flash routes to a separate healthy backend pool.
See ADR-0007 for the full live-probe data.

Still an alias - bump to a dated 3.5-flash suffix for reproducibility
once Google publishes one.
"""

DEFAULT_TIMEOUT_SECONDS = 60.0
"""Wall-clock seconds per LLM call. Caps stuck network requests."""

DEFAULT_MAX_RETRIES = 2
"""LangChain retry count for transient errors (rate limits, 5xx).

Trimmed 5→2 in #163 (pipeline speedup): the prior N=5 stacked badly with
``nick-fields/retry@v4`` at the workflow level, giving up to 10 logical
attempts on a single stuck call. With N=2 the layered worst case is 4 -
still enough to ride out a single 503, but tail latency is bounded.
Override per call when the SLO context differs.
"""

# Per-agent output-token ceilings (issue #163). Calibrated at ~2× the
# observed max across 7 eval samples (rounded up to nearest 500): generous
# enough to avoid truncation, tight enough to bound tail latency. Re-run
# ``evals/run_evals.py`` and bump these if a future prompt change shifts
# the distribution. Reviewer is uncalibrated (not in the eval suite);
# match Story Writer until a calibration run lands.
MAX_OUTPUT_TOKENS_ANALYST = 4000  # observed max 1972 (sample-03)
MAX_OUTPUT_TOKENS_STORY_WRITER = 3000  # observed max 1378 (sample-02)
MAX_OUTPUT_TOKENS_AC_GENERATOR = 4500  # observed max 2042 (sample-06)
MAX_OUTPUT_TOKENS_TEST_CASE_WRITER = 6500  # observed max 3012 (sample-05)
MAX_OUTPUT_TOKENS_REVIEWER = 3000  # uncalibrated - matches Story Writer


def prompt_hash(*prompts: str) -> str:
    """Return a short stable identifier for a set of prompt strings.

    Used as ``prompt_hash`` in LangSmith trace metadata so every traced
    call ties back to the exact prompt version. Editing any prompt or
    reordering arguments changes the hash (both contribute to behaviour).
    First 12 hex chars of sha256: collision-safe for a single project,
    short enough to read in trace UIs.
    """
    h = hashlib.sha256()
    for prompt in prompts:
        h.update(prompt.encode("utf-8"))
        h.update(b"\x00")  # delimiter so ("ab", "c") != ("a", "bc")
    return h.hexdigest()[:12]
