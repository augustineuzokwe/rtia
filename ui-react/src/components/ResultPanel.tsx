import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, downloadArtifactMarkdown } from "@/lib/api";

interface ResultPanelProps {
  threadId: string;
  /** ``payload.rendered_artifact`` — full markdown for the finished story. */
  renderedArtifact: string;
  /** Optional reset hook so the user can kick off another run. */
  onStartOver?: () => void;
}

/**
 * Terminal-state panel for ``DONE``. Renders the final artifact via
 * react-markdown + remark-gfm (so tables / task lists / strikethrough
 * inside the rendered preview survive) and exposes a Download button
 * that hits ``GET /pipeline/{tid}/export.md`` with the bearer token,
 * then saves the response as ``rtia-<tid-short>.md`` via an in-memory
 * object URL.
 *
 * Empty artifact (defensive — the server should always populate one for
 * DONE, but checkpointer truncation or a future split-deep edge case
 * could leave it blank) → friendly fallback rather than a crash.
 */
export function ResultPanel({
  threadId,
  renderedArtifact,
  onStartOver,
}: ResultPanelProps) {
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const onDownload = async () => {
    setError(null);
    setDownloading(true);
    try {
      const blob = await downloadArtifactMarkdown(threadId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `rtia-${threadId.slice(0, 8)}.md`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setError(
          "The server has no artifact for this thread yet. The pipeline " +
            "may have errored before the Composer ran.",
        );
      } else if (e instanceof ApiError) {
        setError(e.message);
      } else if (e instanceof Error) {
        setError(e.message);
      } else {
        setError("Download failed.");
      }
    } finally {
      setDownloading(false);
    }
  };

  const hasArtifact = renderedArtifact.trim().length > 0;

  return (
    <Card data-testid="result-panel">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Final story</CardTitle>
          <CardDescription>
            All four sections — Description, Objective, Acceptance Criteria,
            Test Cases — assembled by the Composer.
          </CardDescription>
        </div>
        <div className="flex flex-shrink-0 gap-2">
          {onStartOver && (
            <Button
              variant="outline"
              onClick={onStartOver}
              data-testid="start-over"
            >
              Start over
            </Button>
          )}
          <Button
            onClick={onDownload}
            disabled={!hasArtifact || downloading}
            data-testid="download-markdown"
          >
            {downloading ? "Preparing…" : "Download .md"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <Alert
            variant="destructive"
            role="alert"
            data-testid="download-error"
          >
            <AlertTitle>Download failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {hasArtifact ? (
          <div
            className="prose prose-sm max-w-none rounded-md border border-border bg-muted/30 p-4 text-sm"
            data-testid="result-preview"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {renderedArtifact}
            </ReactMarkdown>
          </div>
        ) : (
          <p
            className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground"
            data-testid="result-empty"
          >
            The pipeline finished but the server didn't return an artifact.
            That usually means a node in the deep flow short-circuited.
            Check the API log or rerun.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
