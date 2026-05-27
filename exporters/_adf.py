"""Markdown → ADF (Atlassian Document Format) converter for RTIA artifacts.

**Scope** (intentionally narrow - see #223):

Targets the deterministic Markdown shape produced by
``FinalUserStory.as_markdown()`` only:

- ``## Heading`` / ``#### Sub-heading`` lines
- ``- bullet`` (single-level bullet lists)
- ``  N. step`` (single-level numbered lists, used inside test-case
  sections; the leading 2-space indent is part of the rendered shape
  but doesn't change the ADF nesting)
- Plain paragraph lines
- Inline ``**bold**`` and ``_italic_`` spans

Anything outside that shape (tables, nested lists, fenced code blocks,
images, links, blockquotes) is NOT supported - RTIA's artifact doesn't
emit them, and adding handling speculatively would bloat the converter
without proving worth its weight.

**Fallback contract**: the public entry point ``markdown_to_adf`` only
raises ``ValueError`` on truly degenerate input (empty/whitespace
markdown). Callers (the Jira exporter) catch ``Exception`` from this
module and fall back to the legacy ``codeBlock`` wrap so a converter
bug never blocks a Jira push.

Why hand-roll instead of pulling a dependency: the producer is under
our control (``FinalUserStory.as_markdown()``), so a general GFM→ADF
library is overkill. A focused ~150 LOC converter is easier to audit,
test, and evolve with the artifact shape.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBERED_STEP_RE = re.compile(r"^\s*\d+\.\s+\S")

# Bold and italic alternates with named groups so the caller can tell
# them apart without re-parsing the match text. Italic anchors are
# word-boundary aware so intra-word underscores (e.g. ``DEBUG_MODE``)
# are NOT treated as italic delimiters, but italic content MAY contain
# underscores (e.g. ``_(happy_path)_`` from ``TestCase.as_markdown_section``).
_INLINE_MARK_RE = re.compile(
    r"\*\*(?P<bold>[^*\n]+?)\*\*"
    r"|"
    r"(?<!\w)_(?P<italic>[^\n_][^\n]*?)_(?!\w)"
)


def markdown_to_adf(markdown: str) -> dict[str, Any]:
    """Convert RTIA's known Markdown shape to an ADF ``doc`` node.

    Returns the full ``{"type": "doc", "version": 1, "content": [...]}``
    structure ready for ``fields.description`` on Jira's REST v3 API.

    Raises ``ValueError`` only when the input is empty after trimming -
    that's a producer bug worth surfacing rather than silently turning
    into an empty Jira description.
    """
    if not markdown or not markdown.strip():
        raise ValueError("markdown_to_adf: input is empty or whitespace-only")

    lines = markdown.splitlines()
    content: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("#### "):
            content.append(_heading(4, stripped[5:]))
            i += 1
            continue
        if stripped.startswith("### "):
            content.append(_heading(3, stripped[4:]))
            i += 1
            continue
        if stripped.startswith("## "):
            content.append(_heading(2, stripped[3:]))
            i += 1
            continue
        if stripped.startswith("# "):
            content.append(_heading(1, stripped[2:]))
            i += 1
            continue

        if stripped.startswith("- "):
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            content.append(_bullet_list(items))
            continue

        if _NUMBERED_STEP_RE.match(line):
            steps: list[str] = []
            while i < len(lines) and _NUMBERED_STEP_RE.match(lines[i]):
                # Strip the leading whitespace + "N. " prefix.
                s = lines[i].strip()
                _, _, rest = s.partition(". ")
                steps.append(rest)
                i += 1
            content.append(_ordered_list(steps))
            continue

        # Default: paragraph.
        content.append(_paragraph(stripped))
        i += 1

    if not content:
        # Producer wrote whitespace-only sections - degrade to a single
        # paragraph so Jira receives a valid (if empty-looking) doc.
        content = [_paragraph("")]

    return {"type": "doc", "version": 1, "content": content}


def _heading(level: int, text: str) -> dict[str, Any]:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": _inline_runs(text),
    }


def _paragraph(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": _inline_runs(text) if text else []}


def _bullet_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [_list_item(it) for it in items],
    }


def _ordered_list(items: list[str]) -> dict[str, Any]:
    return {
        "type": "orderedList",
        "attrs": {"order": 1},
        "content": [_list_item(it) for it in items],
    }


def _list_item(text: str) -> dict[str, Any]:
    return {
        "type": "listItem",
        "content": [{"type": "paragraph", "content": _inline_runs(text)}],
    }


def _inline_runs(text: str) -> list[dict[str, Any]]:
    """Parse ``**bold**`` and ``_italic_`` spans into ADF text-with-marks runs.

    Plain text outside the marks becomes a bare ``text`` node. Non-greedy
    matching keeps adjacent marks distinct.
    """
    runs: list[dict[str, Any]] = []
    pos = 0
    for m in _INLINE_MARK_RE.finditer(text):
        if m.start() > pos:
            runs.append({"type": "text", "text": text[pos : m.start()]})
        if m.group("bold") is not None:
            runs.append(
                {
                    "type": "text",
                    "text": m.group("bold"),
                    "marks": [{"type": "strong"}],
                }
            )
        else:
            runs.append(
                {
                    "type": "text",
                    "text": m.group("italic"),
                    "marks": [{"type": "em"}],
                }
            )
        pos = m.end()
    if pos < len(text):
        runs.append({"type": "text", "text": text[pos:]})
    if not runs:
        runs = [{"type": "text", "text": text}]
    return runs


__all__ = ["markdown_to_adf"]
