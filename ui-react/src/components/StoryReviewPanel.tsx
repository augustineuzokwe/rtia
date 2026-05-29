import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

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
import { ApiError, resumeThread } from "@/lib/api";
import type { ThreadState } from "@/lib/types";

interface StoryReviewPanelProps {
  threadId: string;
  /** ``payload.rendered_artifact`` — Story Writer's markdown preview. */
  renderedArtifact: string;
  /** ``payload.description`` / ``payload.objective`` — current values to seed
   *  the override textareas. */
  description: string;
  objective: string;
  /** Called with the post-resume thread state. */
  onResumed: (next: ThreadState) => void;
}

type Mode = "preview" | "override";

/**
 * Story Review checkpoint. Two paths:
 *
 * - **Accept as-is**: ``POST /resume { accepted: true }``. The Composer
 *   uses the Story Writer's output unchanged.
 * - **Override**: edit description and/or objective, then
 *   ``POST /resume { accepted: false, description, objective }``. Empty
 *   fields ship as ``null``; the server treats ``null``/missing as
 *   "keep the Story Writer's value", so partial overrides work.
 *
 * The override section is hidden behind a button so the preview-first
 * UX matches the Gradio version: most runs end in Accept.
 */
export function StoryReviewPanel({
  threadId,
  renderedArtifact,
  description,
  objective,
  onResumed,
}: StoryReviewPanelProps) {
  const [mode, setMode] = useState<Mode>("preview");
  const [draftDescription, setDraftDescription] = useState(description);
  const [draftObjective, setDraftObjective] = useState(objective);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<"accept" | "override" | null>(
    null,
  );

  // Re-seed the override drafts when the underlying review payload
  // changes. Polling returns fresh payload objects every tick, so key
  // off the *content* not the reference (see learning #42).
  useEffect(() => {
    setDraftDescription(description);
    setDraftObjective(objective);
    setError(null);
    setMode("preview");
  }, [threadId, description, objective]);

  const onAccept = async () => {
    setError(null);
    setSubmitting("accept");
    try {
      const next = await resumeThread(threadId, { accepted: true });
      onResumed(next);
    } catch (e) {
      setError(humanize(e));
    } finally {
      setSubmitting(null);
    }
  };

  const onOverride = async () => {
    setError(null);
    setSubmitting("override");
    try {
      const next = await resumeThread(threadId, {
        accepted: false,
        // Empty drafts → null per API contract; the server falls back to
        // the Story Writer's existing value.
        description: draftDescription.trim() ? draftDescription : null,
        objective: draftObjective.trim() ? draftObjective : null,
      });
      onResumed(next);
    } catch (e) {
      setError(humanize(e));
    } finally {
      setSubmitting(null);
    }
  };

  const isBusy = submitting !== null;

  return (
    <Card data-testid="story-review">
      <CardHeader>
        <CardTitle>Story review</CardTitle>
        <CardDescription>
          The Story Writer drafted Description + Objective from your
          requirements. Accept to continue to ACs and Test Cases, or
          override either field before the pipeline proceeds.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div
          className="prose prose-sm max-w-none rounded-md border border-border bg-muted/30 p-4 text-sm"
          data-testid="story-preview"
        >
          <ReactMarkdown>{renderedArtifact}</ReactMarkdown>
        </div>

        {mode === "override" && (
          <div className="space-y-4" data-testid="story-override-form">
            <div className="space-y-2">
              <Label htmlFor="override-description">Description</Label>
              <Textarea
                id="override-description"
                data-testid="override-description"
                value={draftDescription}
                onChange={(e) => setDraftDescription(e.target.value)}
                rows={3}
                disabled={isBusy}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="override-objective">Objective</Label>
              <Textarea
                id="override-objective"
                data-testid="override-objective"
                value={draftObjective}
                onChange={(e) => setDraftObjective(e.target.value)}
                rows={3}
                disabled={isBusy}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Leave a field blank to keep the Story Writer's value.
            </p>
          </div>
        )}

        {error && (
          <Alert variant="destructive" role="alert" data-testid="review-error">
            <AlertTitle>Resume failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex flex-wrap items-center justify-end gap-3">
          {mode === "preview" ? (
            <>
              <Button
                variant="outline"
                onClick={() => setMode("override")}
                disabled={isBusy}
                data-testid="open-override"
              >
                Override description / objective
              </Button>
              <Button
                onClick={onAccept}
                disabled={isBusy}
                data-testid="accept-as-is"
              >
                {submitting === "accept" ? "Resuming…" : "Accept as-is"}
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="outline"
                onClick={() => setMode("preview")}
                disabled={isBusy}
                data-testid="cancel-override"
              >
                Cancel
              </Button>
              <Button
                onClick={onOverride}
                disabled={isBusy}
                data-testid="submit-override"
              >
                {submitting === "override" ? "Resuming…" : "Submit override"}
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function humanize(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Resume failed.";
}
