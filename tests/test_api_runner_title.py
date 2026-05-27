"""Tests for ``api.runner._derive_title`` (issue #222).

Pins the short-readable-title contract used by all backend exports.
"""

from __future__ import annotations

import pytest

from api.runner import _derive_title


def test_classic_short_user_story_keeps_clean():
    """The base case - short 'As a/an … I want X' yields just X with
    article stripped + first letter capitalised."""
    out = _derive_title("As a user, I want a dark-mode toggle.")
    assert out == "Dark-mode toggle"


def test_long_description_cuts_at_subordinate_clause():
    """Verbose round-2 case from KAN-4 / #219 - the 'I want …' clause
    has a 'located' sub-clause that should be cut so the title becomes
    the bare noun phrase before it."""
    desc = (
        "As a tester, I want a dark-mode toggle located in the header of "
        "the QA dashboard that respects my system preference on first load."
    )
    out = _derive_title(desc)
    assert out == "Dark-mode toggle"


def test_cut_at_that_clause():
    desc = "As a user, I want a notification banner that disappears after 3 seconds."
    out = _derive_title(desc)
    assert out == "Notification banner"


def test_cut_at_so_clause():
    desc = "As a user, I want an export button so I can download my results."
    out = _derive_title(desc)
    assert out == "Export button"


def test_short_input_with_no_cut_marker_preserved():
    desc = "As a tester, I want a CSV export option."
    out = _derive_title(desc)
    assert out == "CSV export option"


def test_no_i_want_marker_falls_back_to_full_text():
    """When the description doesn't follow the 'I want' pattern, use it
    as-is (still apply article-strip + capitalisation + word-boundary
    truncation)."""
    out = _derive_title("The dashboard should refresh every 30 seconds.")
    assert out == "Dashboard should refresh every 30 seconds"


def test_word_boundary_truncation_for_overlong_phrase():
    """No cut marker triggers; description longer than the 60-char soft
    cap → truncate at last word boundary, append ellipsis."""
    desc = (
        "I want notifications and exports and dashboards and reports and audits "
        "and metrics and graphs"
    )
    out = _derive_title(desc)
    assert out.endswith("…")
    # Title body (without ellipsis) must end on a word boundary.
    assert " " not in out[-3:]  # no trailing partial word
    assert len(out) <= 61  # 60 chars + ellipsis


def test_empty_input_returns_sentinel():
    assert _derive_title("") == "(untitled RTIA export)"
    assert _derive_title("   ") == "(untitled RTIA export)"
    assert _derive_title(None) == "(untitled RTIA export)"  # type: ignore[arg-type]


def test_strips_trailing_period():
    out = _derive_title("As a user, I want a CSV export.")
    assert not out.endswith(".")


def test_collapses_internal_whitespace():
    """Multi-line descriptions sometimes contain doubled or newline
    whitespace; the title shouldn't carry that through."""
    out = _derive_title("As a user,\n\nI want   a  dashboard\n\nrefresh.")
    assert "  " not in out
    assert "\n" not in out


def test_only_cut_when_at_least_two_words_remain():
    """If cutting at a subordinate marker leaves only one word, keep
    the longer form - a 1-word title is usually less informative than
    the verbose original."""
    desc = "As a user, I want X that does something useful."
    out = _derive_title(desc)
    # 'X' alone would be useless - should keep more.
    assert "X" in out
    # And shouldn't truncate to just 'X'.
    assert out != "X"


def test_hard_cap_protects_pathological_single_word():
    """Single 200-char 'word' with no spaces - soft cap can't find a
    word boundary; hard cap must still kick in."""
    desc = "I want " + ("x" * 200)
    out = _derive_title(desc)
    assert len(out) <= 120


@pytest.mark.parametrize(
    "article",
    ["A ", "An ", "The ", "a ", "an ", "the "],
)
def test_leading_article_stripped(article):
    out = _derive_title(f"I want {article}dashboard widget.")
    assert out == "Dashboard widget"
