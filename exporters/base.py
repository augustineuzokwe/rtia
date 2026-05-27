"""Exporter protocol + shared types.

Every backend implements ``Exporter.export(artifact, target, *, dry_run)``
returning an ``ExportResult``. The result always includes the
would-be-sent ``payload`` so the caller can inspect (or surface to the
operator) what was constructed, regardless of whether the HTTP call ran.

v1 contract is deliberately narrow — one issue per export, no
subtask/child decomposition. Mapping ACs to subtasks is a v2 concern.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

BackendName = Literal["jira", "github"]


class ExportTarget(BaseModel):
    """Where to send a single artifact.

    Per-backend fields are optional so one model covers both surfaces;
    each backend's exporter validates the fields it needs and raises
    ``ExportConfigError`` if anything required is missing.
    """

    backend: BackendName

    # Jira fields.
    jira_project_key: str | None = Field(
        default=None,
        description="Jira project key (e.g. 'RTIA'). Required for Jira exports.",
    )
    jira_issue_type: str = Field(
        default="Story",
        description="Jira issue type. Defaults to 'Story'; 'Task' / 'Bug' also valid.",
    )
    jira_parent_key: str | None = Field(
        default=None,
        description=(
            "Optional Jira parent (epic) key to attach the new issue to. Uses "
            "the ``parent`` field, which Jira Cloud's company-managed projects "
            "honor for Epic Link."
        ),
    )

    # GitHub fields.
    github_repo: str | None = Field(
        default=None,
        description="GitHub repo in 'owner/name' form. Required for GitHub exports.",
    )
    github_project_number: int | None = Field(
        default=None,
        description=(
            "Optional GitHub Project (v2) number — if set, the created issue "
            "is added to this project after creation."
        ),
    )
    github_labels: list[str] = Field(
        default_factory=list,
        description="GitHub labels to attach to the created issue.",
    )


class ExportRequest(BaseModel):
    """Body shape for ``POST /pipeline/{thread_id}/export``."""

    target: ExportTarget
    dry_run: bool = Field(
        default=False,
        description=(
            "When true, the exporter constructs the would-be payload but "
            "skips the HTTP call. Returned ExportResult.payload contains "
            "exactly what would have been sent."
        ),
    )
    update_issue_id: str | None = Field(
        default=None,
        description=(
            "Optional existing-issue identifier. When set, the exporter "
            "PATCHes (GitHub) or PUTs (Jira) the existing issue instead "
            "of creating a new one. Closes the duplicate-issue gap when "
            "re-running RTIA on a split placeholder's title (#208). GitHub: "
            "numeric issue number (e.g. ``203``). Jira: issue key (e.g. "
            "``RTIA-42``)."
        ),
    )


class ExportResult(BaseModel):
    """What the exporter returns to the caller (and to the UI)."""

    backend: BackendName
    success: bool
    dry_run: bool
    url: str | None = Field(
        default=None,
        description="URL of the created issue, or None for dry-run / failure.",
    )
    key: str | None = Field(
        default=None,
        description=(
            "Jira issue key (e.g. 'RTIA-42') or GitHub issue number as a "
            "string. None for dry-run / failure."
        ),
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="The payload that was sent (or would have been). Always populated.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable failure message; None on success.",
    )


class ExportConfigError(ValueError):
    """Required configuration (env var or target field) was missing."""


class ExporterTransportError(RuntimeError):
    """The backend's HTTP API returned an unexpected response."""


class Exporter(Protocol):
    """Implemented by ``JiraExporter`` and ``GitHubExporter``."""

    backend: BackendName

    def export(
        self,
        artifact_markdown: str,
        target: ExportTarget,
        *,
        title: str,
        dry_run: bool = False,
    ) -> ExportResult: ...

    def update_issue(
        self,
        issue_id: str,
        artifact_markdown: str,
        target: ExportTarget,
        *,
        title: str,
        dry_run: bool = False,
    ) -> ExportResult:
        """Update an existing issue rather than creating a new one.

        ``issue_id`` is a numeric GitHub issue number (string form, e.g.
        ``"203"``) or a Jira issue key (e.g. ``"RTIA-42"``). The backend
        validates the shape and raises ``ExportConfigError`` on a
        mismatch. Closes the duplicate-issue gap from #208 — when the
        PO re-runs RTIA on a split placeholder's title, the deep artifact
        replaces the placeholder in place instead of creating a sibling.
        """
        ...


def make_exporter(backend: BackendName) -> Exporter:
    """Factory — return the right exporter for a backend name.

    Imported lazily so importing ``exporters`` doesn't pull every
    backend's deps. Each backend module is self-contained and can be
    omitted from a deployment that doesn't use it.
    """
    if backend == "jira":
        from exporters.jira import JiraExporter

        return JiraExporter()
    if backend == "github":
        from exporters.github import GitHubExporter

        return GitHubExporter()
    raise ExportConfigError(f"Unknown backend: {backend!r}")


__all__ = [
    "BackendName",
    "ExportConfigError",
    "ExportRequest",
    "ExportResult",
    "ExportTarget",
    "Exporter",
    "ExporterTransportError",
    "make_exporter",
]
