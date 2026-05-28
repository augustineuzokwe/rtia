import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PoCheckpointDeep } from "@/components/PoCheckpointDeep";
import { StatusBadge } from "@/components/StatusBadge";
import { useThreadPoll } from "@/hooks/useThreadPoll";
import type { ThreadState } from "@/lib/types";

interface RunPanelProps {
  /** Initial state returned from the POST /pipeline call. */
  initial: ThreadState;
}

/**
 * Live view of a running thread. Polls ``GET /pipeline/{tid}`` until a
 * terminal status, then leaves the final state on screen. Phase panels
 * (PO checkpoint, story review, result, export) plug in here in
 * US-19 … US-23 — for now we just show the badge + raw status line so
 * the polling loop has something to render.
 */
export function RunPanel({ initial }: RunPanelProps) {
  const { state, phase, lastError, applyState } = useThreadPoll(
    initial.thread_id,
  );
  // Until the first poll lands, use the state we already have so the
  // badge doesn't flash an empty slot.
  const current = state ?? initial;
  const payload = (current.payload ?? {}) as {
    mode?: string;
    critical_ambiguities?: string[];
  };
  const isPoDeep =
    current.status === "paused_po" && (payload.mode ?? "deep") === "deep";

  return (
    <div className="space-y-6" data-testid="run-panel">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Run in progress</CardTitle>
            <CardDescription>
              Thread{" "}
              <code className="font-mono text-xs">{current.thread_id}</code>
            </CardDescription>
          </div>
          <StatusBadge status={current.status} />
        </CardHeader>
        <CardContent className="space-y-4">
          {phase === "polling" && (
            <p
              className="text-xs text-muted-foreground"
              data-testid="poll-indicator"
            >
              Polling every 2 seconds…
            </p>
          )}
          {phase === "stopped" && (
            <p
              className="text-xs text-muted-foreground"
              data-testid="poll-stopped"
            >
              Terminal status reached. Polling stopped.
            </p>
          )}
          {phase === "transient_error" && (
            <Alert data-testid="poll-transient-error">
              <AlertTitle>Temporary polling issue</AlertTitle>
              <AlertDescription>
                {lastError ?? "Couldn't reach the server."} Backing off and
                retrying.
              </AlertDescription>
            </Alert>
          )}
          {phase === "auth_required" && (
            <Alert
              variant="destructive"
              role="alert"
              data-testid="poll-auth-required"
            >
              <AlertTitle>Sign in again</AlertTitle>
              <AlertDescription>
                The API token is missing or invalid. Reopen the tokenised URL
                printed in the server banner to refresh it.
              </AlertDescription>
            </Alert>
          )}
          {!isPoDeep && (
            <p className="text-sm text-muted-foreground">
              Remaining checkpoint and result panels land in US-20 onward.
              In the meantime the legacy{" "}
              <a className="underline" href="/legacy">
                Gradio UI
              </a>{" "}
              still drives split / story-review / result phases to completion.
            </p>
          )}
        </CardContent>
      </Card>

      {isPoDeep && (
        <PoCheckpointDeep
          threadId={current.thread_id}
          questions={payload.critical_ambiguities ?? []}
          onResumed={applyState}
        />
      )}
    </div>
  );
}
