"""Cross-agent LLM helpers.

Lives here rather than in any single agent module because all four agents
post-Gemini-cutover share the same defensive needs:

- Stripping the optional ``` ```json `` ``` fences Gemini occasionally
  wraps structured-output in despite the prompt's "no fences" instruction.
- Coalescing the LangChain ``message.content`` field — older Gemini
  models returned a plain string here; gemini-3.5-flash and later return
  a list of content blocks (``[{'type': 'text', 'text': '...'}, ...]``).
  ``coerce_response_text`` handles both.

Underscore-prefixed module name marks it as project-private — nothing
outside ``agents/`` should import from it.
"""

from __future__ import annotations

from typing import Any


def coerce_response_text(content: Any) -> str:
    """Return the plain-text payload of a LangChain message ``content`` field.

    Gemini-2.5-flash and older return plain strings; gemini-3.5-flash
    returns a list of content blocks like
    ``[{'type': 'text', 'text': '…', 'extras': {...}}]``. We extract and
    concatenate every ``text`` field from ``type == 'text'`` blocks.

    Non-text blocks (e.g. ``thinking`` if the model ever emits them
    alongside text) are skipped — the agents parse structured JSON from
    the text-only payload.

    Anything that isn't a string or a list of dicts falls back to
    ``str()`` — that's the same coarse behaviour the agents had before
    this helper, and surfaces an obvious malformed value loudly enough
    that the JSON parser downstream will complain.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    return str(content)


def strip_json_fence(raw: str) -> str:
    """Strip an optional ``` ```json … ``` `` fence from an LLM response.

    Gemini occasionally wraps JSON output in a markdown code fence despite
    being told not to. Trim defensively rather than fight the prompt — the
    inner JSON is what we want and a fence at the boundary is harmless to
    remove. Anthropic models tested in this project don't add fences, so
    the helper is a no-op for them. Idempotent: calling on un-fenced input
    returns the input stripped of surrounding whitespace.
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    # Drop the opening fence line (``` or ```json), and the closing fence
    # if present. Anything between is the JSON we want.
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
