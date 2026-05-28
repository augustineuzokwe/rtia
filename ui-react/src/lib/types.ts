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
