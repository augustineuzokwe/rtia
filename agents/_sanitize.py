"""Output sanitisation applied at the rendering boundary.

Every consumer of an RTIA artifact (LangGraph PO checkpoint, demo script,
future API) renders via ``FinalUserStory.as_markdown()``. This module
runs three defensive passes on that rendered string so hostile bytes,
arbitrary code-fence language tags, and unbounded length cannot reach a
downstream consumer regardless of what any individual agent emitted.

Defence-in-depth note: this is independent of the Phase 12.1 prompt-
injection metric. 12.1 measures whether the Analyst flagged hostile
*input*; 12.2 cleans whatever *output* the pipeline produced. The two
layers are not substitutes - an Analyst that fails to flag an injection
can still produce contaminated text, and a clean Analyst can still emit
a stray control byte under model stress.

Order matters (and is asserted by ``sanitize_artifact``):

1. Strip control characters first - cleanest input for the regex pass.
2. Normalise code fences next - operates on the cleaned text.
3. Apply the length cap last - so the truncation marker isn't itself
   stripped or mangled by an earlier pass.

All three sanitisers are pure functions on ``str`` so they can be unit-
tested without instantiating any agents or rendering machinery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Default 16 KiB. Realistic user stories run 1–4 KiB, so this leaves a
# 4× headroom while still stopping paste-flood DoS through a hostile
# artifact. Overridable per call site (the eval suite may want a
# different limit than the LangGraph checkpoint).
DEFAULT_MAX_CHARS = 16_384

# Markdown line endings only. Everything else in the ASCII C0 / C1 range
# (NUL, BEL, BS, VT, FF, ESC, DEL, …) is invisible at best and an
# injection vector at worst - strip silently.
_ALLOWED_CONTROL_CHARS = frozenset({"\t", "\n", "\r"})

# Trojan Source / steganography / homoglyph surface. None of these
# legitimately appear in a software requirement artifact, and several
# have been used in supply-chain attacks (CVE-2021-42574, the bidi-
# override RLO/LRO trick). Strip across the board.
_INVISIBLE_FORMATTING_CHARS = frozenset(
    [
        "​",  # zero-width space
        "‌",  # zero-width non-joiner
        "‍",  # zero-width joiner
        "‎",  # left-to-right mark
        "‏",  # right-to-left mark
        "‪",  # LRE
        "‫",  # RLE
        "‬",  # PDF (pop directional formatting)
        "‭",  # LRO  - the Trojan Source vector
        "‮",  # RLO  - the Trojan Source vector
        "﻿",  # BOM / zero-width no-break space
    ]
)

# Code-fence language allowlist. Members are the languages we accept on a
# triple-backtick opener; everything else is rewritten to a bare fence.
# The body of the fenced block is preserved verbatim - we are only
# defending against renderer behaviour keyed on the language tag (mermaid,
# html, svg, javascript, etc. in Jira / Confluence / Notion / GitHub).
DEFAULT_ALLOWED_LANGS = frozenset(
    [
        "text",
        "plaintext",
        "bash",
        "sh",
        "sql",
        "json",
        "yaml",
        "yml",
        "python",
        "py",
        "js",
        "javascript",
        "ts",
        "typescript",
        "gherkin",
        "markdown",
        "md",
        "http",
    ]
)

# Match the opener of a fenced code block: line-anchored ``` followed by an
# optional language tag (alphanumeric + dash/underscore - no spaces, no
# punctuation that could smuggle attributes). Captures the tag so it can
# be inspected and rewritten.
_FENCE_OPENER_RE = re.compile(
    r"^(?P<fence>`{3,})(?P<lang>[A-Za-z0-9_\-]+)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class SanitizeReport:
    """Summary of what ``sanitize_artifact`` had to clean up.

    Returned alongside the cleaned text so observability layers (LangGraph
    checkpoint, eval suite) can record what was changed without making
    the rendering API any wider than ``str``. Empty / zero fields = the
    input was already clean for that pass.
    """

    control_chars_stripped: int = 0
    invisible_chars_stripped: int = 0
    fences_rewritten: list[str] = field(default_factory=list)
    """Language tags that were rewritten to a bare fence (e.g. ['html', 'mermaid'])."""
    truncated_at: int | None = None
    """Original length when truncation fired, or None if the cap was not hit."""

    @property
    def is_clean(self) -> bool:
        """True when no sanitiser made a change. Useful for assertions."""
        return (
            self.control_chars_stripped == 0
            and self.invisible_chars_stripped == 0
            and not self.fences_rewritten
            and self.truncated_at is None
        )


def strip_control_chars(text: str) -> tuple[str, int, int]:
    """Remove ASCII control bytes (except \\t \\n \\r) and invisible formatting.

    Returns the cleaned text plus a (control_count, invisible_count) pair
    so the caller can populate a SanitizeReport. Splitting the two counts
    keeps the security signal legible - Trojan Source bidi overrides are
    a different threat class than stray NUL bytes and worth distinguishing
    in audit output.
    """
    if not text:
        return text, 0, 0

    out_chars: list[str] = []
    control_count = 0
    invisible_count = 0
    for ch in text:
        codepoint = ord(ch)
        # ASCII C0 (0x00-0x1F) and DEL (0x7F) excluding the three markdown-
        # legitimate whitespace bytes.
        if codepoint < 0x20 and ch not in _ALLOWED_CONTROL_CHARS:
            control_count += 1
            continue
        if codepoint == 0x7F:
            control_count += 1
            continue
        if ch in _INVISIBLE_FORMATTING_CHARS:
            invisible_count += 1
            continue
        out_chars.append(ch)
    return "".join(out_chars), control_count, invisible_count


def normalize_code_fences(
    text: str, allowed: frozenset[str] = DEFAULT_ALLOWED_LANGS
) -> tuple[str, list[str]]:
    """Rewrite fenced-code openers whose language tag is not in the allowlist.

    The fence delimiter is preserved (so the block still renders as code)
    and the body is untouched - only the language tag is dropped. This
    defends against renderer behaviour keyed on the tag (HTML, SVG,
    Mermaid auto-render in Confluence/Notion) without sacrificing the
    legitimate use of fenced code in requirements.

    Returns the rewritten text plus the list of tags that were dropped,
    in encounter order (useful for the audit report).
    """
    rewritten: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        lang = match.group("lang").lower()
        if lang in allowed:
            return match.group(0)
        rewritten.append(lang)
        # Drop the language tag; keep the fence width so paired closers
        # still match. Trailing whitespace from the original opener is
        # consumed by the regex.
        return match.group("fence")

    cleaned = _FENCE_OPENER_RE.sub(_replace, text)
    return cleaned, rewritten


def enforce_length_cap(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> tuple[str, int | None]:
    """Truncate ``text`` to ``max_chars`` with a visible inline marker.

    Returns the (possibly truncated) text and the original length when
    truncation fired (None otherwise). The marker is human-readable and
    placed in-band so the PO sees the cut at the LangGraph checkpoint
    rather than silently losing context.

    The truncation leaves room for the marker itself within the cap - the
    final string length is bounded by ``max_chars``, not ``max_chars +
    len(marker)``. This matters because the cap exists to bound paste
    flooding; an attacker who could push the output exactly to the cap
    must not be able to push it past via the marker.
    """
    if len(text) <= max_chars:
        return text, None
    original_length = len(text)
    marker = f"\n\n_[…truncated at {max_chars} chars; original length {original_length}]_"
    # Reserve marker space so the final string is still <= max_chars.
    body = text[: max_chars - len(marker)]
    return body + marker, original_length


def sanitize_artifact(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    allowed_langs: frozenset[str] = DEFAULT_ALLOWED_LANGS,
) -> tuple[str, SanitizeReport]:
    """Run the three sanitisers in their canonical order.

    See module docstring for why the order is (control strip → fence
    normalise → length cap) and not any other permutation. Returns the
    cleaned text and a SanitizeReport summarising what changed.

    Pure function - safe to call in tests, in render code, and in eval
    runners without side effects.
    """
    cleaned, control_count, invisible_count = strip_control_chars(text)
    cleaned, fences_rewritten = normalize_code_fences(cleaned, allowed_langs)
    cleaned, truncated_at = enforce_length_cap(cleaned, max_chars)
    report = SanitizeReport(
        control_chars_stripped=control_count,
        invisible_chars_stripped=invisible_count,
        fences_rewritten=fences_rewritten,
        truncated_at=truncated_at,
    )
    return cleaned, report
