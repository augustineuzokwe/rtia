import { useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
  "PDF · max 10 MB · pasted/extracted text capped at 200,000 characters";

type UploadKind = "pdf" | "md";

export function IntakePanel({ onStarted }: IntakePanelProps) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<"upload" | "run" | null>(null);
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const mdInputRef = useRef<HTMLInputElement>(null);

  const onUpload = async (kind: UploadKind, file: File | undefined) => {
    if (!file) return;
    setError(null);
    setUploadStatus(null);
    setBusy("upload");
    try {
      const result =
        kind === "pdf" ? await uploadPdf(file) : await uploadMarkdown(file);
      // Both endpoints return the extracted text. PDF flow surfaces a
      // confirmation message like the Gradio version did; Markdown flow
      // also fills the textbox so the PO can edit before running.
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
      if (kind === "pdf" && pdfInputRef.current) pdfInputRef.current.value = "";
      if (kind === "md" && mdInputRef.current) mdInputRef.current.value = "";
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

  return (
    <Card>
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
            placeholder="Paste raw requirements here…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={8}
            disabled={busy === "run"}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="pdf-upload">…or upload a PDF</Label>
            <Input
              id="pdf-upload"
              ref={pdfInputRef}
              type="file"
              accept=".pdf,application/pdf"
              disabled={busy !== null}
              onChange={(e) => onUpload("pdf", e.target.files?.[0])}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="md-upload">…or upload Markdown</Label>
            <Input
              id="md-upload"
              ref={mdInputRef}
              type="file"
              accept=".md,text/markdown"
              disabled={busy !== null}
              onChange={(e) => onUpload("md", e.target.files?.[0])}
            />
          </div>
        </div>

        <p className="text-xs text-muted-foreground">{HELPER_TEXT}</p>

        {uploadStatus && !error && (
          <Alert>
            <AlertTitle>Upload complete</AlertTitle>
            <AlertDescription>{uploadStatus}</AlertDescription>
          </Alert>
        )}

        {error && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>Something went wrong</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex justify-end">
          <Button onClick={onRun} disabled={busy !== null || !text.trim()}>
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
    return e.message;
  }
  if (kind === "pdf") {
    return "PDF upload failed. Check the server logs and try again.";
  }
  if (kind === "md") {
    return "Markdown upload failed. Check the server logs and try again.";
  }
  return e instanceof Error ? e.message : "Unknown error.";
}
