import { useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, resumeThread } from "@/lib/api";
import type { ThreadState } from "@/lib/types";

/** Shape of one entry in ``payload.implied_stories`` from the split interrupt. */
export interface ImpliedStory {
  title: string;
  summary?: string;
}

interface PoCheckpointSplitProps {
  threadId: string;
  /** ``payload.implied_stories`` from the paused thread state. */
  stories: ImpliedStory[];
  /** Called with the post-resume thread state. */
  onResumed: (next: ThreadState) => void;
}

interface RowState {
  /** Analyst-provided title — used as the stable key when the PO renames. */
  original_title: string;
  /** Analyst summary, preserved verbatim. */
  summary: string;
  /** Editable title bound to the Input. */
  title: string;
  /** Whether this story will be sent in ``selected_stories``. */
  kept: boolean;
}

function rowsFrom(stories: ImpliedStory[]): RowState[] {
  return stories.map((s) => ({
    original_title: s.title,
    summary: s.summary ?? "",
    title: s.title,
    kept: true,
  }));
}

/**
 * Split-mode PO checkpoint. Renders one editable row per Analyst-implied
 * story; the PO can untick rows to drop them and edit titles before the
 * fan-out. The preferred API shape is ``selected_stories`` (issue #207):
 * the server uses ``original_title`` to map renamed entries back to the
 * Analyst's matching summary.
 *
 * After the resume call the split path terminates (DONE_SPLIT), so the
 * runner will report the placeholder list via ``state.payload.split_stories``
 * for the result panel (US-22) and ``/export-deferred`` to consume.
 */
export function PoCheckpointSplit({
  threadId,
  stories,
  onResumed,
}: PoCheckpointSplitProps) {
  const [rows, setRows] = useState<RowState[]>(() => rowsFrom(stories));
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Re-seed only when the *content* of the story list changes — not on
  // every polling tick. The 2 s GET returns a fresh ``payload`` object
  // each time, so the ``stories`` array identity flips even when values
  // are identical. Keying off the joined original titles gives a stable
  // dep that won't blow away the PO's pending edits between ticks.
  const storiesKey = stories.map((s) => s.title).join("|");
  useEffect(() => {
    setRows(rowsFrom(stories));
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, storiesKey]);

  const keptCount = useMemo(
    () => rows.reduce((n, r) => n + (r.kept ? 1 : 0), 0),
    [rows],
  );

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      const selected = rows
        .filter((r) => r.kept)
        .map((r) => ({
          title: r.title.trim() || r.original_title, // fall back to the un-edited title rather than send ""
          summary: r.summary,
          original_title: r.original_title,
        }));
      // Preferred ``selected_stories`` shape (#207). Empty list is the
      // Q2 default: the graph fans out everything. Same as Gradio.
      const next = await resumeThread(threadId, {
        selected_stories: selected,
        answers: {},
      });
      onResumed(next);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : e instanceof Error
            ? e.message
            : "Resume failed.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const updateRow = (index: number, patch: Partial<RowState>) =>
    setRows((prev) =>
      prev.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    );

  return (
    <Card data-testid="po-checkpoint-split">
      <CardHeader>
        <CardTitle>PO checkpoint — split</CardTitle>
        <CardDescription>
          The Analyst found {stories.length} implied stories. Untick the ones
          you don't want as placeholders, rename any you'd like to clarify,
          then submit. Each kept row becomes a backlog issue.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <ul className="space-y-4">
          {rows.map((row, i) => {
            const titleId = `split-title-${i}`;
            const checkboxId = `split-keep-${i}`;
            return (
              <li
                key={row.original_title}
                className="rounded-md border border-border p-4"
                data-testid={`split-row-${i}`}
                data-kept={row.kept ? "true" : "false"}
              >
                <div className="flex items-start gap-3">
                  <Checkbox
                    id={checkboxId}
                    data-testid={`split-keep-${i}`}
                    checked={row.kept}
                    onCheckedChange={(checked) =>
                      updateRow(i, { kept: checked === true })
                    }
                    disabled={submitting}
                  />
                  <div className="flex-1 space-y-2">
                    <Label
                      htmlFor={titleId}
                      className="text-xs uppercase tracking-wide text-muted-foreground"
                    >
                      Story title
                    </Label>
                    <Input
                      id={titleId}
                      data-testid={`split-title-${i}`}
                      value={row.title}
                      onChange={(e) => updateRow(i, { title: e.target.value })}
                      disabled={submitting || !row.kept}
                    />
                    {row.summary && (
                      <p className="text-xs text-muted-foreground">
                        {row.summary}
                      </p>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>

        {error && (
          <Alert
            variant="destructive"
            role="alert"
            data-testid="split-error"
          >
            <AlertTitle>Resume failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex items-center justify-between gap-3">
          <p
            className="text-xs text-muted-foreground"
            data-testid="split-summary"
          >
            {keptCount} of {rows.length} stories will become placeholders.
          </p>
          <Button
            onClick={onSubmit}
            disabled={submitting}
            data-testid="split-submit"
          >
            {submitting ? "Resuming…" : "Submit selection"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
