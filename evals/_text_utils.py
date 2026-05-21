"""Shared text-similarity helpers for metrics.

Two metrics depend on these primitives (``tc_coverage_breadth`` and
``intent_keyword_overlap``); per CLAUDE.md §4.6 two concrete consumers
is the threshold for extracting a shared module.

All helpers are deterministic, English-tuned, and free of LLM calls —
they belong to the "programmatic metric" tier in the eval stack.
"""

from __future__ import annotations

import re

# Tokens we don't count toward overlap — pure connective noise. Kept
# small on purpose; expand only with evidence from real eval runs, not
# speculatively. Pronouns + auxiliaries + the most generic role/system
# nouns ("user", "system") that appear in almost every requirement.
STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "and",
        "or",
        "but",
        "if",
        "then",
        "so",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "from",
        "as",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "their",
        "there",
        "i",
        "you",
        "we",
        "they",
        "user",
        "system",
        "should",
        "will",
        "shall",
        "must",
        "can",
        "may",
    }
)


def tokenize(text: str) -> set[str]:
    """Lowercase, alphanumeric-only, stopwords removed."""
    return {t for t in re.findall(r"[a-zA-Z0-9]+", text.lower()) if t not in STOPWORDS}


def token_overlap(reference: str, candidate: str) -> float:
    """Fraction of ``reference``'s meaningful tokens that appear in ``candidate``.

    Asymmetric on purpose. Use this when the question is "does the
    candidate *cover* the reference?" — not "do these two strings have
    similar vocabulary overall". Picking the wrong direction inflates or
    deflates scores; the reference is whichever string defines the bar.

    Returns 0.0 if the reference is empty after tokenisation (the bar
    is vacuous — would be silently passed otherwise).
    """
    ref_tokens = tokenize(reference)
    if not ref_tokens:
        return 0.0
    cand_tokens = tokenize(candidate)
    return len(ref_tokens & cand_tokens) / len(ref_tokens)
