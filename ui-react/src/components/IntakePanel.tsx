import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  ApiError,
  runPipeline,
  uploadMarkdown,
  uploadPdf,
} from "@/lib/api";
import type { ThreadState } from "@/lib/types";

interface IntakePanelProps {
  /** Called once a thread is started; parent advances to the polling view. */
  onStarted: (state: ThreadState) => void;
}

const HELPER_TEXT =
  "PDF or Markdown · max 10 MB · pasted/extracted text capped at 200,000 characters";

type UploadKind = "pdf" | "md";

/** Pick the right endpoint from filename + MIME. Returns null for unsupported files. */
function detectUploadKind(file: File): UploadKind | null {
  const name = file.name.toLowerCase();
  if (name.endsWith(".pdf") || file.type === "application/pdf") return "pdf";
  if (name.endsWith(".md") || name.endsWith(".markdown") || file.type === "text/markdown") {
    return "md";
  }
  return null;
}

export function IntakePanel({ onStarted }: IntakePanelProps) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<"upload" | "run" | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    setUploadStatus(null);
    const kind = detectUploadKind(file);
    if (!kind) {
      setError(
        `Unsupported file type "${file.name}". Upload a .pdf or .md file, or paste the requirement directly.`,
      );
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setBusy("upload");
    try {
      const result =
        kind === "pdf" ? await uploadPdf(file) : await uploadMarkdown(file);
      // Both endpoints return the extracted text — fill the textbox so the
      // PO can edit before running. Surface a count confirmation that says
      // which path ran so a misrouted upload is obvious.
      setText(result.text);
      setUploadStatus(
        kind === "pdf"
          ? `Extracted ${result.char_count.toLocaleString()} characters from PDF.`
          : `Loaded ${result.char_count.toLocaleString()} characters from Markdown.`,
      );
    } catch (e) {
      setError(humanizeError(e, kind));
    } finally {
      setBusy(null);
      // Reset the file input so re-selecting the same file fires onChange again.
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const onRun = async () => {
    setError(null);
    if (!text.trim()) {
      setError("Paste a requirement or upload a file first.");
      return;
    }
    setBusy("run");
    try {
      const state = await runPipeline(text);
      onStarted(state);
    } catch (e) {
      setError(humanizeError(e, null));
    } finally {
      setBusy(null);
    }
  };

  // Run is enabled only when there's something to send. ``text`` is the
  // single source of truth: a successful upload fills it with the
  // extracted content, so this one check covers both "paste" and
  // "upload" entry points. ``busy`` blocks double-submits.
  const canRun = busy === null && text.trim().length > 0;

  return (
    <Card data-testid="intake-panel">
      <CardHeader>
        <CardTitle>Start a run</CardTitle>
        <CardDescription>
          Paste a requirement or upload a PDF / Markdown file. The pipeline
          will pause at the PO and Story Review checkpoints.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Label htmlFor="requirement-text">Requirement text</Label>
          <Textarea
            id="requirement-text"
            data-testid="intake-textarea"
            placeholder="Paste raw requirements here…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={8}
            disabled={busy === "run"}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="file-upload">…or upload a file</Label>
          <label
            htmlFor="file-upload"
            onDragOver={(e) => {
              e.preventDefault();
              if (busy === null) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (busy === null) onUpload(e.dataTransfer.files?.[0]);
            }}
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-input bg-muted/30 px-4 py-6 text-center transition-colors hover:border-primary/50 hover:bg-accent/50",
              dragging && "border-primary bg-accent",
              busy !== null && "pointer-events-none opacity-50",
            )}
          >
            <UploadCloud className="size-6 text-muted-foreground" aria-hidden />
            <span className="text-sm font-medium">
              {busy === "upload" ? "Extracting…" : "Drop a PDF or Markdown file, or click to browse"}
            </span>
            <span className="text-xs text-muted-foreground">.pdf · .md</span>
            <input
              id="file-upload"
              data-testid="intake-file"
              ref={fileInputRef}
              type="file"
              className="sr-only"
              accept=".pdf,.md,.markdown,application/pdf,text/markdown"
              disabled={busy !== null}
              onChange={(e) => onUpload(e.target.files?.[0])}
            />
          </label>
        </div>

        <p className="text-xs text-muted-foreground" data-testid="intake-helper">
          {HELPER_TEXT}
        </p>

        {uploadStatus && !error && (
          <Alert data-testid="intake-upload-status">
            <AlertTitle>Upload complete</AlertTitle>
            <AlertDescription>{uploadStatus}</AlertDescription>
          </Alert>
        )}

        {error && (
          <Alert variant="destructive" role="alert" data-testid="intake-error">
            <AlertTitle>Something went wrong</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex justify-end">
          <Button
            onClick={onRun}
            disabled={!canRun}
            data-testid="intake-run"
          >
            {busy === "run" ? "Starting…" : "Run pipeline"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Map API errors to copy a PO can act on. */
function humanizeError(e: unknown, kind: UploadKind | null): string {
  if (e instanceof ApiError) {
    // The PDF extractor returns a structured code for OCR-only PDFs;
    // surface a focused message instead of the raw "OCR is not supported"
    // sentence so the helper text below still makes sense.
    if (e.code === "scanned_pdf") {
      return "This PDF appears to be a scanned image. OCR is not supported — re-upload a text-based PDF.";
    }
    if (e.status === 413) {
      return "PDF too large. The 10 MB limit keeps a single run cheap.";
    }
    if (e.status === 401) {
      return "API token missing or invalid. Reopen the URL printed in the server banner.";
    }
    // 5xx on the intake calls means the request never reached a working
    // pipeline — almost always the backend (or its Vite proxy) is down,
    // not a real run error (those return 200 with status="error").
    if (e.status >= 500) {
      return "Can't reach the RTIA backend. Is the API server running on :8000?";
    }
    return e.message;
  }
  // fetch() rejects with a TypeError when the network call itself fails
  // (server not listening, CORS, dropped connection) — surface the same
  // actionable hint rather than the cryptic "Failed to fetch".
  if (e instanceof TypeError) {
    return "Can't reach the RTIA backend. Is the API server running on :8000?";
  }
  if (kind === "pdf") {
    return "PDF upload failed. Check the server logs and try again.";
  }
  if (kind === "md") {
    return "Markdown upload failed. Check the server logs and try again.";
  }
  return e instanceof Error ? e.message : "Unknown error.";
}
