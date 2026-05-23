"""Jira Cloud exporter — REST v3 ``POST /issue``.

Auth: basic with ``JIRA_EMAIL`` + ``JIRA_API_TOKEN``, base URL from
``JIRA_BASE_URL`` (e.g. ``https://your-org.atlassian.net``).

Description is sent as ADF (Atlassian Document Format). v1 wraps the
rendered markdown in a single ``codeBlock`` node so the artifact survives
intact in Jira's UI. POs can re-format inside Jira; the alternative —
shipping a Markdown→ADF converter — is heavy and out of scope for v1.

Dry-run is the verification path. With no credentials in the env, you
can still inspect the would-be POST body via ``dry_run=True``.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from exporters.base import (
    ExportConfigError,
    ExporterTransportError,
    ExportResult,
    ExportTarget,
)


class JiraExporter:
    """Jira exporter. One instance per process is fine — it's stateless."""

    backend = "jira"

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        # Allow injection for tests; default to a fresh client per export
        # call (build inside ``export``) so we don't hold an idle
        # connection over the API process's lifetime.
        self._injected_client = http_client

    def export(
        self,
        artifact_markdown: str,
        target: ExportTarget,
        *,
        title: str,
        dry_run: bool = False,
    ) -> ExportResult:
        """Create a Jira issue from the rendered artifact.

        Validates target + env config first so a misconfigured request
        fails with a structured ``ExportConfigError`` (HTTP 400 at the
        endpoint), not a half-built request. The HTTP call is deferred
        until after validation.
        """
        if not target.jira_project_key:
            raise ExportConfigError("Jira export requires target.jira_project_key (e.g. 'RTIA').")

        base_url = (os.environ.get("JIRA_BASE_URL") or "").strip().rstrip("/")
        email = (os.environ.get("JIRA_EMAIL") or "").strip()
        api_token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
        if not dry_run and not (base_url and email and api_token):
            raise ExportConfigError(
                "Jira export requires JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN "
                "in the environment (or use dry_run=true to inspect the payload)."
            )

        payload = _build_payload(target, title, artifact_markdown)

        if dry_run:
            return ExportResult(backend="jira", success=True, dry_run=True, payload=payload)

        client = self._injected_client or httpx.Client(timeout=30.0)
        try:
            auth = base64.b64encode(f"{email}:{api_token}".encode()).decode("ascii")
            response = client.post(
                f"{base_url}/rest/api/3/issue",
                json=payload,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        finally:
            if self._injected_client is None:
                client.close()

        if response.status_code >= 400:
            return ExportResult(
                backend="jira",
                success=False,
                dry_run=False,
                payload=payload,
                error=f"Jira API returned {response.status_code}: {response.text[:500]}",
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ExporterTransportError(
                f"Jira returned non-JSON body: {response.text[:200]}"
            ) from exc

        key = body.get("key")
        url = f"{base_url}/browse/{key}" if key else None
        return ExportResult(
            backend="jira",
            success=True,
            dry_run=False,
            url=url,
            key=key,
            payload=payload,
        )


def _build_payload(target: ExportTarget, title: str, markdown: str) -> dict[str, Any]:
    """Construct the Jira REST v3 issue-create body.

    Description uses an ADF ``codeBlock`` so the markdown survives the
    round-trip through Jira's renderer. ``parent`` is set only when
    ``jira_parent_key`` is supplied — Jira Cloud company-managed
    projects honor ``parent`` for Epic Link; team-managed projects use
    the same field for parent issues.
    """
    fields: dict[str, Any] = {
        "project": {"key": target.jira_project_key},
        "issuetype": {"name": target.jira_issue_type},
        "summary": title,
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "markdown"},
                    "content": [{"type": "text", "text": markdown}],
                }
            ],
        },
    }
    if target.jira_parent_key:
        fields["parent"] = {"key": target.jira_parent_key}
    return {"fields": fields}


__all__ = ["JiraExporter"]
