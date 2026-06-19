import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ApiError, exportArtifact, exportDeferred } from "@/lib/api";
import type {
  ExportBackend,
  ExportResult,
  ExportTarget,
  ThreadStatus,
} from "@/lib/types";

interface ExportPanelProps {
  threadId: string;
  /** Drives which endpoint is invoked + the button copy. */
  status: ThreadStatus;
}

interface FormState {
  backend: ExportBackend;
  // Jira
  jira_project_key: string;
  jira_issue_type: string;
  jira_parent_key: string;
  // GitHub
  github_repo: string;
  github_project_number: string;
  github_labels: string; // comma-separated; split on submit
  // Shared
  dry_run: boolean;
}

const INITIAL: FormState = {
  backend: "github",
  jira_project_key: "",
  jira_issue_type: "Story",
  jira_parent_key: "",
  github_repo: "",
  github_project_number: "",
  github_labels: "",
  dry_run: true,
};

function buildTarget(form: FormState): ExportTarget {
  if (form.backend === "jira") {
    return {
      backend: "jira",
      jira_project_key: form.jira_project_key.trim() || null,
      jira_issue_type: form.jira_issue_type.trim() || "Story",
      jira_parent_key: form.jira_parent_key.trim() || null,
    };
  }
  return {
    backend: "github",
    github_repo: form.github_repo.trim() || null,
    github_project_number: form.github_project_number.trim()
      ? Number(form.github_project_number)
      : null,
    github_labels: form.github_labels
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
}

/**
 * Backlog export panel. Single form drives two endpoints based on the
 * current terminal status:
 *
 * - ``DONE``: POST /export creates one issue from the deep artifact.
 *   Button reads "Push to backlog".
 * - ``DONE_SPLIT``: POST /export-deferred fans out one placeholder per
 *   selected_split_story. Button reads "Create follow-up issues".
 *
 * Hidden on ERROR — see Gradio fix #186 §6.1: nothing useful to push.
 *
 * Dry-run defaults to true so a missing credential never silently
 * creates a live issue while the operator is learning the form.
 */
export function ExportPanel({ threadId, status }: ExportPanelProps) {
  const [form, setForm] = useState<FormState>(INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ExportResult[] | null>(null);
  const [skipped, setSkipped] = useState<string[]>([]);

  const isSplit = status === "done_split";
  const buttonLabel = isSplit ? "Create follow-up issues" : "Push to backlog";

  const onSubmit = async () => {
    setError(null);
    setResults(null);
    setSkipped([]);
    setSubmitting(true);
    try {
      const target = buildTarget(form);
      if (isSplit) {
        const response = await exportDeferred(threadId, {
          target,
          dry_run: form.dry_run,
        });
        setResults(response.results);
        setSkipped(response.skipped);
      } else {
        const result = await exportArtifact(threadId, {
          target,
          dry_run: form.dry_run,
        });
        setResults([result]);
      }
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else if (e instanceof Error) setError(e.message);
      else setError("Export failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card data-testid="export-panel" data-mode={isSplit ? "split" : "deep"}>
      <CardHeader>
        <CardTitle>Push to backlog</CardTitle>
        <CardDescription>
          {isSplit
            ? "One issue per kept placeholder story. Dry-run shows the would-be payload without hitting Jira / GitHub."
            : "Create one Jira or GitHub Issue from the composed artifact. Dry-run is faithful — it returns the exact payload that would be sent."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="backend">Backend</Label>
            <Select
              value={form.backend}
              onValueChange={(v) =>
                setForm((f) => ({ ...f, backend: v as ExportBackend }))
              }
            >
              <SelectTrigger id="backend" data-testid="export-backend">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="github" data-testid="backend-option-github">
                  GitHub Issues
                </SelectItem>
                <SelectItem value="jira" data-testid="backend-option-jira">
                  Jira
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end gap-3">
            <div className="flex items-center gap-2">
              <Switch
                id="dry-run"
                data-testid="export-dry-run"
                checked={form.dry_run}
                onCheckedChange={(v) => setForm((f) => ({ ...f, dry_run: v }))}
                disabled={submitting}
              />
              <Label htmlFor="dry-run" className="text-sm">
                Dry run
              </Label>
            </div>
          </div>
        </div>

        {form.backend === "github" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="github-repo">Repository (owner/name)</Label>
              <Input
                id="github-repo"
                data-testid="github-repo"
                placeholder="acme/rtia"
                value={form.github_repo}
                onChange={(e) =>
                  setForm((f) => ({ ...f, github_repo: e.target.value }))
                }
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="github-project">Project (v2) number</Label>
              <Input
                id="github-project"
                data-testid="github-project"
                placeholder="optional, e.g. 5"
                inputMode="numeric"
                value={form.github_project_number}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    github_project_number: e.target.value,
                  }))
                }
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="github-labels">Labels (comma-separated)</Label>
              <Input
                id="github-labels"
                data-testid="github-labels"
                placeholder="rtia, story"
                value={form.github_labels}
                onChange={(e) =>
                  setForm((f) => ({ ...f, github_labels: e.target.value }))
                }
                disabled={submitting}
              />
            </div>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="jira-project">Project key</Label>
              <Input
                id="jira-project"
                data-testid="jira-project"
                placeholder="RTIA"
                value={form.jira_project_key}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    jira_project_key: e.target.value,
                  }))
                }
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="jira-issuetype">Issue type</Label>
              <Input
                id="jira-issuetype"
                data-testid="jira-issuetype"
                placeholder="Story"
                value={form.jira_issue_type}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    jira_issue_type: e.target.value,
                  }))
                }
                disabled={submitting}
              />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <Label htmlFor="jira-parent">Parent (epic) key</Label>
              <Input
                id="jira-parent"
                data-testid="jira-parent"
                placeholder="optional, e.g. RTIA-42"
                value={form.jira_parent_key}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    jira_parent_key: e.target.value,
                  }))
                }
                disabled={submitting}
              />
            </div>
          </div>
        )}

        {error && (
          <Alert variant="destructive" role="alert" data-testid="export-error">
            <AlertTitle>Export failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex justify-end">
          <Button
            onClick={onSubmit}
            disabled={submitting}
            data-testid="export-submit"
          >
            {submitting ? "Working…" : buttonLabel}
          </Button>
        </div>

        {results && results.length > 0 && (
          <div className="space-y-3" data-testid="export-results">
            <h3 className="text-sm font-semibold">Results</h3>
            <ul className="space-y-2">
              {results.map((r, i) => (
                <li
                  key={i}
                  className="rounded-md border border-border p-3 text-sm"
                  data-testid={`export-result-${i}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Badge
                        variant={r.success ? "default" : "destructive"}
                        className={
                          r.success
                            ? "bg-green-100 text-green-900 hover:bg-green-100"
                            : ""
                        }
                      >
                        {r.success ? "Success" : "Failed"}
                      </Badge>
                      {r.dry_run && (
                        <Badge variant="outline">Dry run</Badge>
                      )}
                      {r.key && (
                        <span className="font-mono text-xs">{r.key}</span>
                      )}
                    </div>
                    {r.url && (
                      <a
                        className="text-xs underline"
                        href={r.url}
                        target="_blank"
                        rel="noreferrer"
                        data-testid={`export-result-url-${i}`}
                      >
                        Open
                      </a>
                    )}
                  </div>
                  {r.error && (
                    <p className="mt-2 text-xs text-destructive">{r.error}</p>
                  )}
                  {r.dry_run && (
                    <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted/40 p-2 text-[11px] leading-snug">
                      {JSON.stringify(r.payload, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
            {skipped.length > 0 && (
              <p
                className="text-xs text-muted-foreground"
                data-testid="export-skipped"
              >
                Skipped (not found in deferred set):{" "}
                {skipped.join(", ")}
              </p>
            )}
          </div>
        )}

        {results && results.length === 0 && (
          <p
            className="text-xs text-muted-foreground"
            data-testid="export-no-deferred"
          >
            No deferred stories to push for this thread.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
