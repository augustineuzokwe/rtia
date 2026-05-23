"""Tests for the ``POST /pipeline/{thread_id}/export`` endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agents.final_artifact import FinalUserStory
from api.main import create_app

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


def _stub_artifact() -> FinalUserStory:
    return FinalUserStory(
        description="As a user, I want to do thing X.",
        objective="Outcome Y achieved.",
        acceptance_criteria=[],
        test_cases=[],
        assumptions=[],
    )


def test_export_404_when_no_artifact(client, runner_mock):
    runner_mock.get_artifact_and_title.return_value = None
    r = client.post(
        "/pipeline/tid/export",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 404


def test_export_dry_run_returns_payload(client, runner_mock):
    runner_mock.get_artifact_and_title.return_value = (
        _stub_artifact(),
        "do thing X",
    )
    r = client.post(
        "/pipeline/tid/export",
        headers=_auth(),
        json={
            "target": {"backend": "github", "github_repo": "owner/name"},
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["dry_run"] is True
    assert body["payload"]["title"] == "do thing X"
    assert body["payload"]["body"].startswith("## Description")


def test_export_400_on_config_error(client, runner_mock):
    runner_mock.get_artifact_and_title.return_value = (_stub_artifact(), "t")
    # GitHub backend without required github_repo → ExportConfigError → 400.
    r = client.post(
        "/pipeline/tid/export",
        headers=_auth(),
        json={"target": {"backend": "github"}, "dry_run": True},
    )
    assert r.status_code == 400


def test_export_jira_dry_run_includes_project_key(client, runner_mock):
    runner_mock.get_artifact_and_title.return_value = (_stub_artifact(), "t")
    r = client.post(
        "/pipeline/tid/export",
        headers=_auth(),
        json={
            "target": {"backend": "jira", "jira_project_key": "RTIA"},
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    payload = r.json()["payload"]
    assert payload["fields"]["project"]["key"] == "RTIA"
