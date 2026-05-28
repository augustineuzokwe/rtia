"""Tests for ``POST /pipeline/{thread_id}/export-deferred``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agents.requirements_analyst import ImpliedStory
from api.main import create_app
from api.models import ThreadState, ThreadStatus

TOKEN = "tok"


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def runner_mock():
    return MagicMock()


@pytest.fixture
def client(runner_mock):
    app = create_app(runner=runner_mock, token=TOKEN)
    return TestClient(app)


def test_export_deferred_404_when_thread_missing(client, runner_mock):
    runner_mock.get_deferred_stories_and_context.return_value = None
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 404


def test_export_deferred_empty_returns_zero_results(client, runner_mock):
    runner_mock.get_deferred_stories_and_context.return_value = ([], "the requirement")
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == []
    assert body["skipped"] == []


def test_export_deferred_dry_run_creates_one_payload_per_story(client, runner_mock):
    runner_mock.get_deferred_stories_and_context.return_value = (
        [
            ImpliedStory(title="Story A", summary="A summary"),
            ImpliedStory(title="Story B", summary="B summary"),
            ImpliedStory(title="Story C", summary="C summary"),
        ],
        "Original requirement text covering multiple features.",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 3
    titles = [r["payload"]["title"] for r in body["results"]]
    assert titles == ["Story A", "Story B", "Story C"]
    # Each follow-up body contains the story summary AND the provenance
    # block pointing back to the originating requirement.
    a_body = body["results"][0]["payload"]["body"]
    assert "A summary" in a_body
    assert "Provenance" in a_body
    assert "Originating requirement" in a_body


def test_export_deferred_include_filter_subset(client, runner_mock):
    runner_mock.get_deferred_stories_and_context.return_value = (
        [
            ImpliedStory(title="Story A", summary="A"),
            ImpliedStory(title="Story B", summary="B"),
            ImpliedStory(title="Story C", summary="C"),
        ],
        "req",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "include": ["story a", "Story C"],
            "dry_run": True,
        },
    )
    body = r.json()
    titles = sorted(r["payload"]["title"] for r in body["results"])
    assert titles == ["Story A", "Story C"]
    assert body["skipped"] == []


def test_export_deferred_skipped_titles_reported(client, runner_mock):
    runner_mock.get_deferred_stories_and_context.return_value = (
        [ImpliedStory(title="Story A", summary="A")],
        "req",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "include": ["Story A", "Nonexistent Story"],
            "dry_run": True,
        },
    )
    body = r.json()
    assert [r["payload"]["title"] for r in body["results"]] == ["Story A"]
    assert "nonexistent story" in body["skipped"]


def test_export_deferred_400_on_misconfigured_target(client, runner_mock):
    runner_mock.get_deferred_stories_and_context.return_value = (
        [ImpliedStory(title="Story A", summary="A")],
        "req",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={"target": {"backend": "github"}, "dry_run": True},
    )
    assert r.status_code == 400


def test_export_deferred_jira_dry_run_uses_native_adf(client, runner_mock):
    """#223 - deferred-export Jira path uses the same native-ADF
    converter as the main create path. The follow-up-issue markdown
    starts with ``## Story A`` so the first ADF node is a level-2 heading."""
    runner_mock.get_deferred_stories_and_context.return_value = (
        [ImpliedStory(title="Story A", summary="A")],
        "req",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "jira", "jira_project_key": "RTIA"},
            "dry_run": True,
        },
    )
    body = r.json()
    result = body["results"][0]
    assert result["payload"]["fields"]["summary"] == "Story A"
    desc = result["payload"]["fields"]["description"]
    first = desc["content"][0]
    assert first["type"] == "heading"
    assert first["attrs"]["level"] == 2
    assert first["content"][0]["text"] == "Story A"


def test_export_deferred_dispatches_to_split_source_on_done_split(client, runner_mock):
    """when status is DONE_SPLIT, the endpoint sources stories
    from get_split_stories_and_context, not get_deferred_stories_and_context."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid",
        status=ThreadStatus.DONE_SPLIT,
        payload={"split_stories": [{"title": "Story A", "summary": "a"}]},
    )
    runner_mock.get_split_stories_and_context.return_value = (
        [ImpliedStory(title="Story A", summary="a")],
        "req",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert [r["payload"]["title"] for r in body["results"]] == ["Story A"]
    # Confirm dispatch went through the split branch.
    runner_mock.get_split_stories_and_context.assert_called_once_with("tid")
    runner_mock.get_deferred_stories_and_context.assert_not_called()


def test_export_deferred_uses_deferred_source_on_done_status(client, runner_mock):
    """regression guard - deep DONE threads still use the old source."""
    runner_mock.get_state.return_value = ThreadState(
        thread_id="tid", status=ThreadStatus.DONE, payload={}
    )
    runner_mock.get_deferred_stories_and_context.return_value = (
        [ImpliedStory(title="Story X", summary="x")],
        "req",
    )
    r = client.post(
        "/pipeline/tid/export-deferred",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    runner_mock.get_deferred_stories_and_context.assert_called_once_with("tid")
    runner_mock.get_split_stories_and_context.assert_not_called()
