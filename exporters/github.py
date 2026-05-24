"""GitHub issue exporter — REST API ``POST /repos/{owner}/{repo}/issues``.

Auth: ``GITHUB_TOKEN`` from the environment. A classic PAT with ``repo``
scope works; a fine-grained PAT scoped to the target repo with
``Issues: Read & write`` works too.

If ``target.github_project_number`` is set, the created issue is added
to that GitHub Project (v2) board via the GraphQL ``addProjectV2ItemById``
mutation. The two operations are NOT atomic — if issue-create succeeds
and project-add fails, the result reports the issue URL + an error noting
the project-add failure. Better than swallowing it; better than rolling
back a created issue.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from exporters.base import (
    ExportConfigError,
    ExporterTransportError,
    ExportResult,
    ExportTarget,
)

_GITHUB_API = "https://api.github.com"


class GitHubExporter:
    """GitHub issue + (optional) Project (v2) add."""

    backend = "github"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._injected_client = http_client

    def export(
        self,
        artifact_markdown: str,
        target: ExportTarget,
        *,
        title: str,
        dry_run: bool = False,
    ) -> ExportResult:
        if not target.github_repo or "/" not in target.github_repo:
            raise ExportConfigError(
                "GitHub export requires target.github_repo in 'owner/name' form."
            )

        token = (os.environ.get("GITHUB_TOKEN") or "").strip()
        if not dry_run and not token:
            raise ExportConfigError(
                "GitHub export requires GITHUB_TOKEN in the environment "
                "(or use dry_run=true to inspect the payload). Fine-grained "
                "PAT with Issues: Read & write scope is sufficient."
            )

        payload: dict[str, Any] = {
            "title": title,
            "body": artifact_markdown,
        }
        if target.github_labels:
            payload["labels"] = list(target.github_labels)

        if dry_run:
            payload["_meta"] = {
                "repo": target.github_repo,
                "project_number": target.github_project_number,
            }
            return ExportResult(backend="github", success=True, dry_run=True, payload=payload)

        client = self._injected_client or httpx.Client(timeout=30.0)
        try:
            owner, repo = target.github_repo.split("/", 1)
            response = client.post(
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code >= 400:
                return ExportResult(
                    backend="github",
                    success=False,
                    dry_run=False,
                    payload=payload,
                    error=(
                        f"GitHub issue-create returned {response.status_code}: "
                        f"{response.text[:500]}"
                    ),
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise ExporterTransportError(
                    f"GitHub returned non-JSON body: {response.text[:200]}"
                ) from exc

            issue_url = body.get("html_url")
            issue_node_id = body.get("node_id")
            issue_number = body.get("number")

            project_error: str | None = None
            if target.github_project_number is not None and issue_node_id:
                project_error = _add_to_project(
                    client,
                    token,
                    owner=owner,
                    project_number=target.github_project_number,
                    issue_node_id=issue_node_id,
                )

            return ExportResult(
                backend="github",
                success=True,
                dry_run=False,
                url=issue_url,
                key=str(issue_number) if issue_number is not None else None,
                payload=payload,
                error=project_error,
            )
        finally:
            if self._injected_client is None:
                client.close()

    def update_issue(
        self,
        issue_id: str,
        artifact_markdown: str,
        target: ExportTarget,
        *,
        title: str,
        dry_run: bool = False,
    ) -> ExportResult:
        """PATCH an existing GitHub issue with the new title + body.

        Maps to ``PATCH /repos/{owner}/{repo}/issues/{number}``. Skips
        the optional Project (v2) add — the existing issue is already
        wherever it was originally created. Returns
        ``ExportResult(success=True, key=str(number), url=...)`` on
        success; for 404 / other 4xx, returns ``success=False`` with
        the response body in ``error``.
        """
        if not target.github_repo or "/" not in target.github_repo:
            raise ExportConfigError(
                "GitHub update requires target.github_repo in 'owner/name' form."
            )
        number = (issue_id or "").strip()
        if not number.isdigit():
            raise ExportConfigError(
                f"GitHub update_issue requires a numeric issue number, got {issue_id!r}."
            )

        token = (os.environ.get("GITHUB_TOKEN") or "").strip()
        if not dry_run and not token:
            raise ExportConfigError(
                "GitHub update requires GITHUB_TOKEN in the environment "
                "(or use dry_run=true to inspect the payload)."
            )

        payload: dict[str, Any] = {
            "title": title,
            "body": artifact_markdown,
        }
        owner, repo = target.github_repo.split("/", 1)

        if dry_run:
            payload["_meta"] = {
                "repo": target.github_repo,
                "issue_number": number,
                "operation": "update",
            }
            return ExportResult(backend="github", success=True, dry_run=True, payload=payload)

        client = self._injected_client or httpx.Client(timeout=30.0)
        try:
            response = client.patch(
                f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{number}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if response.status_code >= 400:
                return ExportResult(
                    backend="github",
                    success=False,
                    dry_run=False,
                    payload=payload,
                    error=(
                        f"GitHub issue-update returned {response.status_code}: "
                        f"{response.text[:500]}"
                    ),
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise ExporterTransportError(
                    f"GitHub returned non-JSON body: {response.text[:200]}"
                ) from exc
            return ExportResult(
                backend="github",
                success=True,
                dry_run=False,
                url=body.get("html_url"),
                key=str(body.get("number")) if body.get("number") is not None else number,
                payload=payload,
            )
        finally:
            if self._injected_client is None:
                client.close()


def _add_to_project(
    client: httpx.Client,
    token: str,
    *,
    owner: str,
    project_number: int,
    issue_node_id: str,
) -> str | None:
    """Add an issue node to a user-owned GitHub Project (v2) via GraphQL.

    Returns ``None`` on success, or a human-readable error string. The
    error is reported on the otherwise-successful ExportResult — we
    don't unwind issue creation just because the project-add failed.
    """
    lookup = client.post(
        f"{_GITHUB_API}/graphql",
        json={
            "query": """
            query($owner: String!, $number: Int!) {
              user(login: $owner) {
                projectV2(number: $number) { id }
              }
              organization(login: $owner) {
                projectV2(number: $number) { id }
              }
            }
            """,
            "variables": {"owner": owner, "number": project_number},
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if lookup.status_code >= 400:
        return f"project lookup returned {lookup.status_code}"
    data = lookup.json().get("data") or {}
    project_id = ((data.get("user") or {}).get("projectV2") or {}).get("id") or (
        (data.get("organization") or {}).get("projectV2") or {}
    ).get("id")
    if not project_id:
        return f"project {owner}/{project_number} not found via GraphQL"

    add = client.post(
        f"{_GITHUB_API}/graphql",
        json={
            "query": """
            mutation($projectId: ID!, $contentId: ID!) {
              addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
                item { id }
              }
            }
            """,
            "variables": {"projectId": project_id, "contentId": issue_node_id},
        },
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if add.status_code >= 400:
        return f"project add returned {add.status_code}"
    errors = add.json().get("errors")
    if errors:
        return f"project add errors: {errors!r}"
    return None


__all__ = ["GitHubExporter"]
