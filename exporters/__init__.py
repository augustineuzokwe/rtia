"""Exporters - push a FinalUserStory to an external backlog (Phase 15.2).

Two backends in v1: Jira (Atlassian Cloud, REST v3) and GitHub (issue +
project add). Both behind one Protocol so the API endpoint + UI button
don't branch per backend.

Use ``make_exporter(backend)`` to construct one. ``dry_run=True`` on the
``export()`` call short-circuits the HTTP request and returns the
would-be payload for inspection - safe to use without credentials.
"""

from exporters.base import (
    Exporter,
    ExportRequest,
    ExportResult,
    ExportTarget,
    make_exporter,
)
from exporters.github import GitHubExporter
from exporters.jira import JiraExporter

__all__ = [
    "ExportRequest",
    "ExportResult",
    "ExportTarget",
    "Exporter",
    "GitHubExporter",
    "JiraExporter",
    "make_exporter",
]
