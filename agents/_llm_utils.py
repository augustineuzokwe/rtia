"""Cross-agent LLM helpers.

Lives here rather than in any single agent module because all four agents
post-Gemini-cutover share the same defensive needs: stripping the optional
``` ```json `` ``` fences Gemini occasionally wraps structured-output in
despite the prompt's "no fences" instruction.

Underscore-prefixed module name marks it as project-private — nothing
outside ``agents/`` should import from it.
"""

from __future__ import annotations


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
