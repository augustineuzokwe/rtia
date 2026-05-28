"""Secret-pattern detection on incoming requirement text.

Scans raw user input for high-confidence API key / token patterns BEFORE
any external call. If a match is found, the pipeline refuses to run:
no Gemini call, no LangSmith trace, no render. The user is shown
redacted findings and told to redact / rotate before re-submitting.

**Why block rather than warn.** Once a high-confidence pattern is
detected, sending the text to Gemini or LangSmith is malpractice -
both vendors persist their logs, and the secret is effectively
exfiltrated the moment we transmit it. See #124 for the discussion.

**Why prefix-anchored regex, not entropy.** A generic
``[A-Za-z0-9]{32,}`` would fire on every UUID, SHA digest, and random
ID in legitimate requirements. Each pattern is keyed on a
vendor-specific prefix (``AKIA``, ``sk-ant-``, ``AIza``, ``ghp_``, ...)
unlikely to occur outside its issuer.

**Adding a pattern.** Append to ``_PATTERNS`` with a descriptive name
and compiled regex; add positive + negative tests in
``tests/test_secret_scan.py``; confirm the 7 sample files still scan
clean (regression-tested in the same file).

**What this module does NOT do:**

- Scrub or modify input. Inputs pass unchanged or are rejected
  outright - silent modification would feed the LLM different text
  than the user thinks they submitted.
- Make outbound network calls (no HIBP / pwned-key lookup). Pure
  regex, zero latency, zero new dependency.
- Cover every possible secret format. It covers the highest-impact
  prefix-anchored vendor patterns that account for the bulk of
  accidental-paste leaks.
- Replace the pre-commit secret scanner (``detect-secrets``, #126).
  That defends committed files; this defends runtime input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Pattern:
    """One entry in the secret pattern registry.

    Kept private - callers use ``scan_for_secrets`` rather than touching
    the registry directly.
    """

    name: str
    """Identifier surfaced in audit output (e.g. 'anthropic_api_key')."""
    regex: re.Pattern[str]


# Pattern registry. Order is irrelevant - all patterns run on the same
# input. Each regex must be specific enough that legitimate UUIDs /
# hashes / random IDs will not match.
_PATTERNS: list[_Pattern] = [
    # AWS access key IDs: AKIA + 16 base32 chars. Secret access keys
    # (40-char base64) have no distinguishing prefix and would
    # false-positive on every random base64 blob - excluded by design.
    _Pattern(name="aws_access_key_id", regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # GitHub PATs (classic + fine-grained + OAuth + refresh + server +
    # user-to-server). 4-char prefix + 36+ base62 chars.
    # ``github_pat_`` is the fine-grained PAT prefix (longer payload).
    _Pattern(
        name="github_pat",
        regex=re.compile(
            r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|"
            r"\bgithub_pat_[A-Za-z0-9_]{82,}\b"
        ),
    ),
    # Anthropic API keys: sk-ant-<api/admin>-<40+ base62/-/_>.
    _Pattern(
        name="anthropic_api_key",
        regex=re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
    ),
    # OpenAI: legacy sk- keys are 48 chars after the prefix; project
    # keys start with sk-proj- and have a longer payload. Both matched.
    _Pattern(
        name="openai_api_key",
        regex=re.compile(r"\bsk-[A-Za-z0-9]{48}\b|" r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    ),
    # Google API keys (Maps, AI Studio, etc.): AIza + 35 base64url chars.
    _Pattern(
        name="google_api_key",
        regex=re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ),
    # Slack tokens: bot, app, user, refresh, etc. Five-segment dash-separated.
    _Pattern(
        name="slack_token",
        regex=re.compile(r"\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[0-9a-zA-Z]{24,34}\b"),
    ),
    # Stripe live keys: secret, publishable, restricted. Test-mode keys
    # (sk_test_, pk_test_, rk_test_) are intentionally NOT flagged -
    # they are non-production credentials and routinely appear in docs.
    _Pattern(
        name="stripe_live_key",
        regex=re.compile(r"\b(sk|pk|rk)_live_[A-Za-z0-9]{24,}\b"),
    ),
    # JWT: three base64url segments separated by dots. The first two
    # must start with ``ey`` (base64url of a JSON object's opening
    # ``{"``) - tight enough to avoid colliding with hyphenated IDs.
    _Pattern(
        name="jwt",
        regex=re.compile(r"\bey[A-Za-z0-9_-]{8,}\.ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
    # PEM private keys. Match the header line; body irrelevant for
    # detection. ``DOTALL`` not needed since we only need the header.
    _Pattern(
        name="pem_private_key",
        regex=re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
    ),
]


@dataclass(frozen=True)
class SecretFinding:
    """One match from ``scan_for_secrets``.

    NEVER stores the raw matched secret - only the redacted form. The
    raw secret is discarded at scan time so it cannot leak via logging,
    repr, or error formatting downstream.

    ``start`` / ``end`` are byte offsets into the original text, useful
    for cursor positioning in a future UI but never sufficient to
    reconstruct the secret on their own.
    """

    pattern_name: str
    redacted_match: str
    """Redacted form (first 4 + last 4 chars, middle masked). Safe to
    print to logs, stderr, and user-facing error messages."""
    start: int
    end: int


class SecretInInputError(RuntimeError):
    """Raised by ``raise_if_secrets_found`` when the input contains a secret.

    Carries the redacted findings list for callers (demo script, graph
    start node, API handlers) to format a user-facing error. The
    exception message contains only redacted forms.
    """

    def __init__(self, findings: list[SecretFinding]) -> None:
        self.findings = findings
        summary = ", ".join(f"{f.pattern_name} ({f.redacted_match})" for f in findings)
        super().__init__(
            f"Input contains {len(findings)} potential secret(s): {summary}. "
            "Pipeline refused before any external call. Redact / rotate the "
            "credential(s) and re-submit."
        )


def _redact(matched: str) -> str:
    """Return a safe-to-log representation of ``matched``.

    Shows first 4 + last 4 chars with the middle masked. Short matches
    (≤ 8 chars) are fully masked because revealing any character of a
    short token reveals too much of it.

    Examples::

        _redact("sk-ant-abcdef1234567890wxyz") -> "sk-a…wxyz"
        _redact("AKIAABCDEFGHIJKLMNOP")       -> "AKIA…MNOP"
        _redact("short")                       -> "…"
    """
    if len(matched) <= 8:
        return "…"
    return f"{matched[:4]}…{matched[-4:]}"


def scan_for_secrets(text: str) -> list[SecretFinding]:
    """Return one ``SecretFinding`` per match across all registered patterns.

    Pure function. No I/O, no LLM, no state. The raw matched text is
    redacted at construction time and discarded - only the redacted form
    survives in the returned dataclass, so the findings list is safe to
    log, render, or persist to LangSmith metadata.

    Multiple findings of the same pattern are returned separately.
    """
    findings: list[SecretFinding] = []
    for pattern in _PATTERNS:
        for match in pattern.regex.finditer(text):
            findings.append(
                SecretFinding(
                    pattern_name=pattern.name,
                    redacted_match=_redact(match.group(0)),
                    start=match.start(),
                    end=match.end(),
                )
            )
    # Sort by position so callers see findings in reading order.
    findings.sort(key=lambda f: f.start)
    return findings


def raise_if_secrets_found(text: str) -> None:
    """Raise ``SecretInInputError`` if any registered pattern matches.

    Called at every input boundary (demo script, graph start node, API
    handlers). Returning without raising means the caller is free to
    invoke the pipeline - the block-before-external-call policy is
    enforced by calling this here.
    """
    findings = scan_for_secrets(text)
    if findings:
        raise SecretInInputError(findings)
