import { useState } from "react";

import { IntakePanel } from "@/components/IntakePanel";
import { RunPanel } from "@/components/RunPanel";
import type { ThreadState } from "@/lib/types";

export default function App() {
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

        {thread ? <RunPanel initial={thread} /> : <IntakePanel onStarted={setThread} />}

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
