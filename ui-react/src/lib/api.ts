// Thin typed client over the FastAPI surface in api/main.py.
//
// Auth model mirrors the Gradio era: the printed startup URL embeds the
// bearer token as ?token=…; we cache it in localStorage so refreshes
// don't lock the user out, and send it as an Authorization header on
// every fetch (the cookie middleware already authenticates same-origin
// asset fetches separately).
//
// Errors are normalised to a single ``ApiError`` so the UI can render
// human-readable text instead of raw JSON.

import type {
  ThreadState,
  ThreadStatus,
  UploadResult,
} from "@/lib/types";

const TOKEN_STORAGE_KEY = "rtia_token";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** Read the bearer token: ?token=… on first visit, then localStorage. */
export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("token");
  if (fromQuery) {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, fromQuery);
    // Strip the token from the visible URL to avoid leaking via screenshots
    // or referrers. localStorage still has it for the next fetch.
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
    return fromQuery;
  }
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Best-effort decode of FastAPI's various 4xx body shapes into one message. */
async function decodeError(response: Response): Promise<ApiError> {
  let code: string | null = null;
  let message = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      // ScannedPdfError shape: {error: "scanned_pdf", message: "..."}
      if (typeof detail.message === "string") message = detail.message;
      if (typeof detail.error === "string") code = detail.error;
    } else if (Array.isArray(body?.detail)) {
      // Pydantic ValidationError shape — pick the first issue.
      const first = body.detail[0];
      if (first?.msg) message = first.msg;
    }
  } catch {
    // Body wasn't JSON; keep the status-line message.
  }
  return new ApiError(message, response.status, code);
}

async function jsonRequest<T>(input: RequestInfo, init: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    headers: { ...(init.headers ?? {}), ...authHeaders() },
  });
  if (!response.ok) throw await decodeError(response);
  return (await response.json()) as T;
}

/** ``POST /pipeline`` — kick off a new run. */
export async function runPipeline(requirementText: string): Promise<ThreadState> {
  return jsonRequest<ThreadState>("/pipeline", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ requirement_text: requirementText }),
  });
}

/** ``GET /pipeline/{thread_id}`` — current state for polling. */
export async function getThreadState(threadId: string): Promise<ThreadState> {
  return jsonRequest<ThreadState>(
    `/pipeline/${encodeURIComponent(threadId)}`,
    { method: "GET" },
  );
}

/** ``POST /uploads/pdf`` — server extracts text and returns it + char count. */
export async function uploadPdf(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  return jsonRequest<UploadResult>("/uploads/pdf", {
    method: "POST",
    body: form,
  });
}

/** ``POST /uploads/markdown`` — server reads the file and returns its text. */
export async function uploadMarkdown(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  return jsonRequest<UploadResult>("/uploads/markdown", {
    method: "POST",
    body: form,
  });
}

export type { ThreadState, ThreadStatus, UploadResult };
