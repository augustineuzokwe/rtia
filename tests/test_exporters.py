"""Tests for the Jira + GitHub exporters.

HTTP is mocked at the httpx level (transport mock so request inspection
+ programmable responses both work). No live network in unit tests.
"""

from __future__ import annotations

import httpx
import pytest

from exporters.base import ExportConfigError, ExportTarget, make_exporter
from exporters.github import GitHubExporter
from exporters.jira import JiraExporter

# ---------- Jira --------------------------------------------------------


def test_jira_dry_run_builds_payload_without_creds(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)

    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    result = JiraExporter().export(
        "## Description\nbody", target, title="As a user I want X", dry_run=True
    )

    assert result.success is True
    assert result.dry_run is True
    assert result.payload["fields"]["project"]["key"] == "RTIA"
    assert result.payload["fields"]["summary"] == "As a user I want X"
    # ADF codeBlock preserves the markdown text verbatim.
    desc = result.payload["fields"]["description"]
    assert desc["type"] == "doc"
    block = desc["content"][0]
    assert block["type"] == "codeBlock"
    assert block["content"][0]["text"].startswith("## Description")


def test_jira_dry_run_includes_parent_when_set():
    target = ExportTarget(backend="jira", jira_project_key="RTIA", jira_parent_key="RTIA-1")
    result = JiraExporter().export("body", target, title="t", dry_run=True)
    assert result.payload["fields"]["parent"]["key"] == "RTIA-1"


def test_jira_missing_project_key_raises():
    target = ExportTarget(backend="jira")
    with pytest.raises(ExportConfigError):
        JiraExporter().export("body", target, title="t", dry_run=True)


def test_jira_missing_creds_for_real_run_raises(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    with pytest.raises(ExportConfigError):
        JiraExporter().export("body", target, title="t", dry_run=False)


def test_jira_real_post_succeeds(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret")

    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(201, json={"id": "10001", "key": "RTIA-42"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    result = JiraExporter(http_client=client).export("body", target, title="t", dry_run=False)

    assert result.success is True
    assert result.key == "RTIA-42"
    assert result.url == "https://example.atlassian.net/browse/RTIA-42"
    assert len(seen_requests) == 1
    assert "Basic " in seen_requests[0].headers["authorization"]


def test_jira_real_post_returns_error_on_4xx(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret")

    transport = httpx.MockTransport(
        lambda r: httpx.Response(400, text='{"errors":{"project":"required"}}')
    )
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    result = JiraExporter(http_client=client).export("body", target, title="t", dry_run=False)

    assert result.success is False
    assert "400" in result.error


# ---------- GitHub ------------------------------------------------------


def test_github_dry_run_builds_payload_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    target = ExportTarget(
        backend="github",
        github_repo="acme/widgets",
        github_labels=["rtia", "needs-triage"],
    )
    result = GitHubExporter().export("## body", target, title="As a user I want X", dry_run=True)

    assert result.success is True
    assert result.dry_run is True
    assert result.payload["title"] == "As a user I want X"
    assert result.payload["body"] == "## body"
    assert result.payload["labels"] == ["rtia", "needs-triage"]
    assert result.payload["_meta"]["repo"] == "acme/widgets"


def test_github_missing_repo_raises():
    target = ExportTarget(backend="github")
    with pytest.raises(ExportConfigError):
        GitHubExporter().export("body", target, title="t", dry_run=True)


def test_github_real_post_creates_issue(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues"):
            return httpx.Response(
                201,
                json={
                    "html_url": "https://github.com/acme/widgets/issues/7",
                    "node_id": "I_abcdef",
                    "number": 7,
                },
            )
        return httpx.Response(500, text="unexpected path")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="github", github_repo="acme/widgets")
    result = GitHubExporter(http_client=client).export("body", target, title="t", dry_run=False)

    assert result.success is True
    assert result.url == "https://github.com/acme/widgets/issues/7"
    assert result.key == "7"
    assert result.error is None


def test_github_project_add_failure_surfaces_in_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues"):
            return httpx.Response(201, json={"html_url": "u", "node_id": "I_abc", "number": 9})
        # GraphQL lookup returns no project under either user/org.
        return httpx.Response(200, json={"data": {"user": None, "organization": None}})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    target = ExportTarget(
        backend="github",
        github_repo="acme/widgets",
        github_project_number=42,
    )
    result = GitHubExporter(http_client=client).export("body", target, title="t", dry_run=False)

    # Issue was still created — the result reports the project-add error
    # but success=True for the issue itself.
    assert result.success is True
    assert result.key == "9"
    assert result.error is not None
    assert "project" in result.error.lower()


# ---------- factory -----------------------------------------------------


def test_make_exporter_routes_by_backend():
    assert make_exporter("jira").backend == "jira"
    assert make_exporter("github").backend == "github"
    with pytest.raises(ExportConfigError):
        make_exporter("teamcity")  # type: ignore[arg-type]
