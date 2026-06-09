import { useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { Hero } from "@/components/Hero";
import { IntakePanel } from "@/components/IntakePanel";
import { RunPanel } from "@/components/RunPanel";
import { RunSummary } from "@/components/RunSummary";
import type { ThreadState } from "@/lib/types";

interface ActiveRun {
  state: ThreadState;
  startedAt: number; // epoch ms — used by the collapsed summary
}

const APP_VERSION = "v1.1.0";
const REPO_URL = "https://github.com/augustineuzokwe/rtia";

export default function App() {
  const [run, setRun] = useState<ActiveRun | null>(null);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <AppHeader />
      <main className="mx-auto max-w-[1100px] space-y-10 px-4 pb-16 pt-8">
        {run ? (
          <div className="space-y-6">
            <RunSummary
              threadId={run.state.thread_id}
              startedAt={run.startedAt}
            />
            <RunPanel initial={run.state} onStartOver={() => setRun(null)} />
          </div>
        ) : (
          <>
            <Hero />
            <IntakePanel
              onStarted={(state) => setRun({ state, startedAt: Date.now() })}
            />
          </>
        )}

        <footer className="border-t border-border pt-4 text-xs text-muted-foreground">
          RTIA {APP_VERSION} · MIT ·{" "}
          <a
            className="underline decoration-dotted underline-offset-2 hover:text-foreground"
            href={REPO_URL}
            target="_blank"
            rel="noreferrer"
          >
            github.com/augustineuzokwe/rtia
          </a>{" "}
          · Legacy{" "}
          <a className="underline" href="/legacy">
            Gradio UI
          </a>{" "}
          available during the Epic 6 migration.
        </footer>
      </main>
    </div>
  );
}
