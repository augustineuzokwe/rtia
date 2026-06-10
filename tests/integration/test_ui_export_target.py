"""Tests for ``ui.gradio_app._build_export_target``.

Pins the mapping from the three Backlog-target Textbox values onto an
``ExportTarget``. The round-1 walk-through left this hop unverified
because the operator (Claude) forgot to fill the project-number field
during the live UI test - the wiring was always correct, but there was
no regression net. Issue #215.
"""

from __future__ import annotations

from ui.gradio_app import _build_export_target

# ---------- GitHub --------------------------------------------------------


def test_github_with_repo_and_project_number():
    tgt = _build_export_target("github", "augustineuzokwe/rtia", "5")
    assert tgt.backend == "github"
    assert tgt.github_repo == "augustineuzokwe/rtia"
    assert tgt.github_project_number == 5


def test_github_project_number_empty_means_none():
    """Empty extra → no project add. Matches the UI default."""
    tgt = _build_export_target("github", "augustineuzokwe/rtia", "")
    assert tgt.github_project_number is None


def test_github_project_number_non_digit_swallowed_as_none():
    """The Textbox accepts anything; only digit strings become a project
    number. A non-digit value silently degrades to "no project add" -
    matches the pre-extraction handler behaviour."""
    tgt = _build_export_target("github", "augustineuzokwe/rtia", "five")
    assert tgt.github_project_number is None


def test_github_repo_empty_means_none():
    tgt = _build_export_target("github", "", "")
    assert tgt.github_repo is None


def test_github_strips_whitespace_from_extra():
    """Operators sometimes paste with a leading/trailing space; we don't
    want a stray space to silently disable the project add."""
    tgt = _build_export_target("github", "augustineuzokwe/rtia", "  5  ")
    assert tgt.github_project_number == 5


# ---------- Jira ----------------------------------------------------------


def test_jira_with_project_key_only():
    tgt = _build_export_target("jira", "RTIA", "")
    assert tgt.backend == "jira"
    assert tgt.jira_project_key == "RTIA"
    assert tgt.jira_parent_key is None


def test_jira_with_parent_epic():
    tgt = _build_export_target("jira", "RTIA", "RTIA-1")
    assert tgt.jira_project_key == "RTIA"
    assert tgt.jira_parent_key == "RTIA-1"


def test_jira_strips_whitespace():
    tgt = _build_export_target("jira", "  RTIA  ", "  RTIA-1  ")
    assert tgt.jira_project_key == "RTIA"
    assert tgt.jira_parent_key == "RTIA-1"


# ---------- None-safety ---------------------------------------------------


def test_handles_none_inputs():
    """Gradio Textbox values can arrive as None when empty in some
    component states; the helper must not crash."""
    tgt = _build_export_target("github", None, None)  # type: ignore[arg-type]
    assert tgt.github_repo is None
    assert tgt.github_project_number is None
