import { useState } from "react";

interface RunSummaryProps {
  threadId: string;
  /** Epoch ms when the run was started — captured in App when POST returns. */
  startedAt: number;
}

/**
 * Single-line collapsed summary of the active run that lives where the
 * intake panel used to sit. Replaces the verbose "Run in progress" card
 * header in the polish pass (US-25). Collapse content (an expand
 * disclosure) shows the full thread id and the absolute timestamp so a
 * long ``thread_id`` never breaks the row layout.
 */
export function RunSummary({ threadId, startedAt }: RunSummaryProps) {
  const [expanded, setExpanded] = useState(false);
  const startedDate = new Date(startedAt);
  const timeShort = startedDate.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const timeAbsolute = startedDate.toLocaleString();
  const tidShort = threadId.slice(0, 8);

  return (
    <section
      data-testid="run-summary"
      data-expanded={expanded ? "true" : "false"}
      className="rounded-md border border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate">
          Run started at{" "}
          <span className="font-medium" data-testid="run-summary-time">
            {timeShort}
          </span>{" "}
          ·{" "}
          <code className="font-mono text-[11px]">{tidShort}…</code>
        </span>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-[11px] underline decoration-dotted underline-offset-2 hover:text-foreground"
          data-testid="run-summary-toggle"
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      {expanded && (
        <dl
          data-testid="run-summary-expanded"
          className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1"
        >
          <dt className="text-[11px] uppercase tracking-wide">Thread</dt>
          <dd className="break-all font-mono text-[11px] text-foreground">
            {threadId}
          </dd>
          <dt className="text-[11px] uppercase tracking-wide">Started</dt>
          <dd className="text-[11px] text-foreground">{timeAbsolute}</dd>
        </dl>
      )}
    </section>
  );
}
