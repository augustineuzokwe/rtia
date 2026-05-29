// Shapes mirrored 1:1 from api/models.py. Keep these in sync when the
// Pydantic models change. There's no codegen step today — the surface
// is small enough that hand-maintenance is cheaper than a generator.

export type ThreadStatus =
  | "running"
  | "paused_po"
  | "paused_review"
  | "done"
  | "done_split"
  | "error";

export interface ThreadState {
  thread_id: string;
  status: ThreadStatus;
  payload: Record<string, unknown>;
}

export interface UploadResult {
  text: string;
  char_count: number;
}

// Mirrors ``exporters/base.py``. Kept hand-maintained alongside the
// Python contract; the surface is tiny and changes rarely.

export type ExportBackend = "jira" | "github";

export interface ExportTarget {
  backend: ExportBackend;
  jira_project_key?: string | null;
  jira_issue_type?: string;
  jira_parent_key?: string | null;
  github_repo?: string | null;
  github_project_number?: number | null;
  github_labels?: string[];
}

export interface ExportRequest {
  target: ExportTarget;
  dry_run?: boolean;
  update_issue_id?: string | null;
}

export interface ExportResult {
  backend: ExportBackend;
  success: boolean;
  dry_run: boolean;
  url: string | null;
  key: string | null;
  payload: Record<string, unknown>;
  error: string | null;
}

export interface DeferredExportResponse {
  results: ExportResult[];
  skipped: string[];
}
