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
    # #223 - description is now native ADF (heading + paragraph) rather
    # than a codeBlock wrap. Markdown→ADF conversion lives in
    # exporters/_adf.py; this assertion pins the integration point.
    desc = result.payload["fields"]["description"]
    assert desc["type"] == "doc"
    first = desc["content"][0]
    assert first["type"] == "heading"
    assert first["attrs"]["level"] == 2
    assert first["content"][0]["text"] == "Description"
    second = desc["content"][1]
    assert second["type"] == "paragraph"
    assert second["content"][0]["text"] == "body"


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

    # Issue was still created - the result reports the project-add error
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


# ---------- update_issue (#208) -----------------------------------------


def test_github_update_dry_run_builds_patch_payload(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    target = ExportTarget(backend="github", github_repo="acme/widgets")
    result = GitHubExporter().update_issue(
        "203", "## new body", target, title="Updated title", dry_run=True
    )
    assert result.success is True
    assert result.dry_run is True
    assert result.payload["title"] == "Updated title"
    assert result.payload["body"] == "## new body"
    assert result.payload["_meta"]["operation"] == "update"
    assert result.payload["_meta"]["issue_number"] == "203"


def test_github_update_requires_numeric_id():
    target = ExportTarget(backend="github", github_repo="acme/widgets")
    with pytest.raises(ExportConfigError):
        GitHubExporter().update_issue("RTIA-42", "body", target, title="t", dry_run=True)


def test_github_update_real_patch_succeeds(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PATCH" and request.url.path.endswith("/issues/203"):
            return httpx.Response(
                200,
                json={
                    "html_url": "https://github.com/acme/widgets/issues/203",
                    "number": 203,
                },
            )
        return httpx.Response(500, text=f"unexpected: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="github", github_repo="acme/widgets")
    result = GitHubExporter(http_client=client).update_issue(
        "203", "body", target, title="t", dry_run=False
    )
    assert result.success is True
    assert result.key == "203"
    assert result.url == "https://github.com/acme/widgets/issues/203"
    assert len(seen) == 1
    assert seen[0].method == "PATCH"


def test_github_update_404_returns_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    transport = httpx.MockTransport(lambda r: httpx.Response(404, text='{"message":"Not Found"}'))
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="github", github_repo="acme/widgets")
    result = GitHubExporter(http_client=client).update_issue(
        "999", "body", target, title="t", dry_run=False
    )
    assert result.success is False
    assert "404" in result.error


def test_jira_update_dry_run_builds_put_payload(monkeypatch):
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    result = JiraExporter().update_issue(
        "RTIA-42", "## body", target, title="Updated", dry_run=True
    )
    assert result.success is True
    assert result.dry_run is True
    assert result.payload["fields"]["summary"] == "Updated"
    # #223 - update path uses the same native-ADF converter as create.
    first = result.payload["fields"]["description"]["content"][0]
    assert first["type"] == "heading"
    assert first["attrs"]["level"] == 2
    assert first["content"][0]["text"] == "body"
    # No project / issuetype / parent on update - those are pinned at create.
    assert "project" not in result.payload["fields"]
    assert "issuetype" not in result.payload["fields"]
    assert result.payload["_meta"]["operation"] == "update"


def test_jira_update_requires_key_shape():
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    with pytest.raises(ExportConfigError):
        JiraExporter().update_issue("123", "body", target, title="t", dry_run=True)


def test_jira_update_real_put_succeeds(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        # Jira returns 204 No Content on a successful update.
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    result = JiraExporter(http_client=client).update_issue(
        "RTIA-42", "body", target, title="t", dry_run=False
    )
    assert result.success is True
    assert result.key == "RTIA-42"
    assert result.url == "https://example.atlassian.net/browse/RTIA-42"
    assert seen[0].method == "PUT"


def test_jira_update_4xx_returns_error(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret")
    transport = httpx.MockTransport(lambda r: httpx.Response(404, text='{"errorMessages":["x"]}'))
    client = httpx.Client(transport=transport)
    target = ExportTarget(backend="jira", jira_project_key="RTIA")
    result = JiraExporter(http_client=client).update_issue(
        "RTIA-999", "body", target, title="t", dry_run=False
    )
    assert result.success is False
    assert "404" in result.error
