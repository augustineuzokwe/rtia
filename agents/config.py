"""Shared agent configuration constants.

Single source of truth for cross-agent defaults (model ID, timeouts,
retries, token caps). See ADR-0001/0006/0007 for the provider history.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

LLM_PROVIDER_ENV_VAR = "RTIA_LLM_PROVIDER"
"""Selects the chat-LLM provider. Values: ``google`` (default), ``ollama``,
or ``fake``.

- ``google`` → ``ChatGoogleGenerativeAI`` (Gemini Flash); production default.
- ``ollama`` → ``ChatOllama`` against a local Ollama server; see
  ``docs/ollama-probe-2026-05-26.md``.
- ``fake`` → :class:`agents._fake_llm.FakeChatModel`; deterministic, zero-cost
  canned-fixture responses for UI/E2E testing. See
  ``docs/adr-0015-fake-llm-provider.md``.

Any other value raises ``ValueError`` at provider-check time (fail fast on
typos rather than silently falling back to Google). ADR-0006 originally
picked "one consumer per import site"; ADR-0015 keeps that shape when
adding the third value.
"""

_VALID_PROVIDERS = frozenset({"google", "ollama", "fake"})

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

OLLAMA_HOST_ENV_VAR = "RTIA_OLLAMA_HOST"
"""Optional remote Ollama URL (e.g. ``http://nas.local:11435``).

When unset, ``langchain-ollama`` defaults to ``http://localhost:11434`` -
the historical behaviour since ADR-0007 introduced the Ollama path.
Set this to point RTIA at a remote Ollama server such as a NAS-hosted
deployment behind a reverse proxy. See ``docs/nas-ollama-setup.md`` for
the canonical remote setup.
"""

OLLAMA_AUTH_TOKEN_ENV_VAR = "RTIA_OLLAMA_AUTH_TOKEN"
"""Optional Bearer token for the remote Ollama URL.

Ollama itself has no auth - this token is meant for a reverse proxy
(e.g. Caddy) sitting in front of Ollama. When set together with
``RTIA_OLLAMA_HOST``, RTIA adds ``Authorization: Bearer <token>`` to
outgoing requests via langchain-ollama's ``client_kwargs`` hook
(verified against langchain-ollama 1.1.0's ``chat_models.py:715``).

Setting this without ``RTIA_OLLAMA_HOST`` is a no-op - the localhost
default has no proxy to authenticate against, and silently adding the
header to a localhost request would surprise the operator.
"""

_OLLAMA_JUDGE_TRUTHY = {"1", "true", "yes", "on"}


def _llm_provider() -> str:
    """Return the configured provider name, lowercased, with strict validation.

    Raises ``ValueError`` if the env var is set to anything outside
    :data:`_VALID_PROVIDERS`. Unset / empty falls back to ``"google"``
    (production default unchanged from v1.0.0). Validation happens here
    (one place) so every caller of :func:`use_ollama` / :func:`use_fake`
    benefits without each agent re-validating.
    """
    raw = os.environ.get(LLM_PROVIDER_ENV_VAR, "google").strip().lower()
    if raw not in _VALID_PROVIDERS:
        raise ValueError(
            f"Invalid {LLM_PROVIDER_ENV_VAR}={raw!r}; valid values: {sorted(_VALID_PROVIDERS)}"
        )
    return raw


def use_ollama() -> bool:
    """Return True when the process is configured to use the Ollama provider."""
    return _llm_provider() == "ollama"


def use_fake() -> bool:
    """Return True when the process is configured to use the fake provider.

    See :mod:`agents._fake_llm` and ``docs/adr-0015-fake-llm-provider.md``.
    Each agent's ``_build_llm`` checks this first; falls through to the
    existing Ollama / Google branches when not set.
    """
    return _llm_provider() == "fake"


def use_ollama_judge() -> bool:
    """Return True when the deepeval judge should run through Ollama.

    Independent of :func:`use_ollama` - generator and judge have separate
    switches so the judge default stays on Gemini regardless of generator.
    """
    raw = os.environ.get(OLLAMA_JUDGE_ENV_VAR, "").strip().lower()
    return raw in _OLLAMA_JUDGE_TRUTHY


def ollama_remote_kwargs() -> dict:
    """Build optional ChatOllama kwargs for a remote, authenticated Ollama.

    Returns ``{}`` when ``RTIA_OLLAMA_HOST`` is unset so the localhost
    default behaviour (historical since ADR-0007) is preserved
    byte-identically. When the host is set, returns ``base_url=<host>``.
    When ``RTIA_OLLAMA_AUTH_TOKEN`` is *also* set, adds the Bearer header
    via langchain-ollama's ``client_kwargs`` plumbing (verified against
    langchain-ollama 1.1.0's ``chat_models.py:693, 715``).

    Token-without-host is a deliberate no-op - the localhost default has
    no proxy to authenticate against, and silently adding a header to a
    request to ``localhost:11434`` would surprise the operator. The
    operator must opt into the remote path before the token is sent.
    """
    host = os.environ.get(OLLAMA_HOST_ENV_VAR, "").strip()
    token = os.environ.get(OLLAMA_AUTH_TOKEN_ENV_VAR, "").strip()
    if not host:
        return {}
    kwargs: dict = {"base_url": host}
    if token:
        kwargs["client_kwargs"] = {"headers": {"Authorization": f"Bearer {token}"}}
    return kwargs


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
"""Canonical Gemini model used by all pipeline agents.

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
