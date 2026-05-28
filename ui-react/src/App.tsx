import { useState } from "react";

import { IntakePanel } from "@/components/IntakePanel";
import type { ThreadState } from "@/lib/types";

export default function App() {
  // US-17 stops at "the run started." US-18 picks up from ``thread`` and
  // polls. For now we just confirm the call succeeded so the PO sees
  // something has happened.
  const [thread, setThread] = useState<ThreadState | null>(null);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="container mx-auto max-w-3xl space-y-8 py-10">
        <header className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">RTIA</h1>
          <p className="text-muted-foreground">
            Requirements → backlog-ready user story.
          </p>
        </header>

        {thread ? (
          <section className="rounded-lg border border-border bg-card p-6 text-card-foreground">
            <p className="text-sm font-medium">
              Run started ({thread.thread_id.slice(0, 8)}…) — status{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                {thread.status}
              </code>
            </p>
            <p className="mt-2 text-sm text-muted-foreground">
              Polling and checkpoint panels land in US-18 onward. In the
              meantime the legacy{" "}
              <a className="underline" href="/legacy">
                Gradio UI
              </a>{" "}
              still drives runs to completion.
            </p>
          </section>
        ) : (
          <IntakePanel onStarted={setThread} />
        )}

        <footer className="text-xs text-muted-foreground">
          Legacy Gradio UI available at{" "}
          <a className="underline" href="/legacy">
            /legacy
          </a>{" "}
          while Epic 6 is in flight.
        </footer>
      </div>
    </main>
  );
}
