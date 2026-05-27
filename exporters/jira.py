"""Jira Cloud exporter - REST v3 ``POST /issue``.

Auth: basic with ``JIRA_EMAIL`` + ``JIRA_API_TOKEN``, base URL from
``JIRA_BASE_URL`` (e.g. ``https://your-org.atlassian.net``).

Description is sent as ADF (Atlassian Document Format). v1 wraps the
rendered markdown in a single ``codeBlock`` node so the artifact survives
intact in Jira's UI. POs can re-format inside Jira; the alternative -
shipping a Markdown→ADF converter - is heavy and out of scope for v1.

Dry-run is the verification path. With no credentials in the env, you
can still inspect the would-be POST body via ``dry_run=True``.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from exporters._adf import markdown_to_adf
from exporters.base import (
    ExportConfigError,
    ExporterTransportError,
    ExportResult,
    ExportTarget,
)

_log = logging.getLogger("rtia.exporters.jira")


class JiraExporter:
    """Jira exporter. One instance per process is fine - it's stateless."""

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

    def update_issue(
        self,
        issue_id: str,
        artifact_markdown: str,
        target: ExportTarget,
        *,
        title: str,
        dry_run: bool = False,
    ) -> ExportResult:
        """PUT an existing Jira issue's summary + description.

        Maps to ``PUT /rest/api/3/issue/{key}`` with the standard
        ``{"fields": {...}}`` body shape. Jira returns ``204 No Content``
        on success - we synthesise the resulting URL from
        ``JIRA_BASE_URL`` + the issue key. Project / issuetype / parent
        are intentionally NOT included in the update body - they were
        set at create time and Jira validates updates against the
        existing issue's context.
        """
        key = (issue_id or "").strip()
        if not key or "-" not in key:
            raise ExportConfigError(
                f"Jira update_issue requires an issue key like 'RTIA-42', got {issue_id!r}."
            )

        base_url = (os.environ.get("JIRA_BASE_URL") or "").strip().rstrip("/")
        email = (os.environ.get("JIRA_EMAIL") or "").strip()
        api_token = (os.environ.get("JIRA_API_TOKEN") or "").strip()
        if not dry_run and not (base_url and email and api_token):
            raise ExportConfigError(
                "Jira update requires JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN "
                "in the environment (or use dry_run=true to inspect the payload)."
            )

        # #223 - same native-ADF conversion as the create path. Update
        # bodies must match what create sends so an update doesn't
        # silently regress a previously-ADF-rendered description into a
        # codeBlock wall.
        payload: dict[str, Any] = {
            "fields": {
                "summary": title,
                "description": _description_adf(artifact_markdown),
            }
        }

        if dry_run:
            payload["_meta"] = {"issue_key": key, "operation": "update"}
            return ExportResult(backend="jira", success=True, dry_run=True, payload=payload)

        client = self._injected_client or httpx.Client(timeout=30.0)
        try:
            auth = base64.b64encode(f"{email}:{api_token}".encode()).decode("ascii")
            response = client.put(
                f"{base_url}/rest/api/3/issue/{key}",
                json={"fields": payload["fields"]},
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

        # 204 No Content on success - no body to parse. Synthesise URL.
        url = f"{base_url}/browse/{key}"
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

    Description is converted from RTIA's Markdown shape to native ADF
    (#223). Native ADF renders as proper headings, lists, and inline
    bold/italic in Jira instead of a static code-block wall.

    ``parent`` is set only when ``jira_parent_key`` is supplied - Jira
    Cloud company-managed projects honor ``parent`` for Epic Link;
    team-managed projects use the same field for parent issues.
    """
    fields: dict[str, Any] = {
        "project": {"key": target.jira_project_key},
        "issuetype": {"name": target.jira_issue_type},
        "summary": title,
        "description": _description_adf(markdown),
    }
    if target.jira_parent_key:
        fields["parent"] = {"key": target.jira_parent_key}
    return {"fields": fields}


def _description_adf(markdown: str) -> dict[str, Any]:
    """Convert ``markdown`` to an ADF ``doc`` for Jira's description field.

    Falls back to the legacy ``codeBlock`` wrap if the converter raises
    - keeping a push working through a parser bug is more valuable than
    a clean ADF (we'll see and fix the bug from logs). #223 contract.
    """
    try:
        return markdown_to_adf(markdown)
    except Exception:
        _log.warning(
            "rtia.exporters.jira.adf_fallback",
            extra={"event": "adf_fallback"},
            exc_info=True,
        )
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "markdown"},
                    "content": [{"type": "text", "text": markdown}],
                }
            ],
        }


__all__ = ["JiraExporter"]
