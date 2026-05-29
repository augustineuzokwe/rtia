import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Shape of ``payload.error`` for ERROR threads — mirrors
 * ``agents/_llm_errors.py::LLMFailureDetail``. Each field is optional
 * defensively because older threads or non-LLM failures may omit some.
 */
export interface PipelineErrorDetail {
  agent?: string;
  error_class?: string;
  http_status?: number | null;
  retries_attempted?: number;
  message?: string;
}

interface ErrorPanelProps {
  threadId: string;
  /** ``payload.error`` from the ERROR thread state. */
  error?: PipelineErrorDetail | Record<string, unknown> | null;
  /** Stub artifact the server placed in ``payload.rendered_artifact``
   *  for ERROR threads — shown inside the details disclosure. */
  stubArtifact?: string;
  onStartOver?: () => void;
}

const FALLBACK_MESSAGE = "Pipeline failed, no details available.";

/**
 * Terminal-state panel for ``ERROR``. Renders the failing agent + the
 * underlying error class + the human-readable message as the headline,
 * then tucks the full ``payload.error`` JSON + the stub artifact into a
 * native ``<details>`` disclosure so the operator can drill in without
 * the panel ballooning.
 *
 * The Result + Export panels are intentionally **not** rendered when
 * this one is on screen — Gradio fix #186 §6.1: don't surface a
 * "Push to backlog" form for a stub artifact that the Composer never
 * built. ``Start new run`` resets the thread state via the parent's
 * ``onStartOver`` callback, which also tears down the polling loop
 * cleanly through the ``threadId`` change in ``useThreadPoll``.
 */
export function ErrorPanel({
  threadId,
  error,
  stubArtifact,
  onStartOver,
}: ErrorPanelProps) {
  const [showDetails, setShowDetails] = useState(false);

  const detail = (error ?? {}) as PipelineErrorDetail;
  const message = (detail.message ?? "").trim() || FALLBACK_MESSAGE;
  const agent = detail.agent;
  const errorClass = detail.error_class;
  const httpStatus = detail.http_status;
  const retries = detail.retries_attempted;

  const hasError = error && typeof error === "object" && Object.keys(error).length > 0;

  return (
    <Card data-testid="error-panel" className="border-destructive">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="text-destructive">Pipeline failed</CardTitle>
          <CardDescription>
            Thread <code className="font-mono text-xs">{threadId}</code> couldn't
            complete. The artifact wasn't composed, so the backlog export is
            unavailable.
          </CardDescription>
        </div>
        {onStartOver && (
          <Button onClick={onStartOver} data-testid="error-start-over">
            Start new run
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        <p
          className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive-foreground"
          data-testid="error-message"
        >
          <span className="font-medium text-destructive">{message}</span>
        </p>

        {(agent || errorClass || httpStatus != null || retries != null) && (
          <div className="flex flex-wrap gap-2" data-testid="error-meta">
            {agent && (
              <Badge variant="outline" data-testid="error-agent">
                Agent: <span className="ml-1 font-mono">{agent}</span>
              </Badge>
            )}
            {errorClass && (
              <Badge variant="outline" data-testid="error-class">
                Class: <span className="ml-1 font-mono">{errorClass}</span>
              </Badge>
            )}
            {httpStatus != null && (
              <Badge variant="outline">HTTP {httpStatus}</Badge>
            )}
            {retries != null && retries > 0 && (
              <Badge variant="outline">Retries: {retries}</Badge>
            )}
          </div>
        )}

        {(hasError || stubArtifact) && (
          <div className="space-y-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowDetails((v) => !v)}
              data-testid="error-toggle-details"
            >
              {showDetails ? "Hide details" : "Show details"}
            </Button>
            {showDetails && (
              <div className="space-y-3" data-testid="error-details">
                {hasError && (
                  <pre
                    className="max-h-48 overflow-auto rounded-md bg-muted/40 p-3 text-[11px] leading-snug"
                    data-testid="error-raw"
                  >
                    {JSON.stringify(error, null, 2)}
                  </pre>
                )}
                {stubArtifact && (
                  <pre
                    className="max-h-48 overflow-auto rounded-md bg-muted/40 p-3 text-[11px] leading-snug"
                    data-testid="error-stub"
                  >
                    {stubArtifact}
                  </pre>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
