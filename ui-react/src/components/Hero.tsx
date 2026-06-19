import { ArrowRight } from "lucide-react";

import { Fragment } from "react";

import { cn } from "@/lib/utils";

// The deep-path pipeline, surfaced so a first-time visitor understands what
// "Requirements → user story" actually runs through before they paste anything.
// "Backlog" is the export destination (Jira / GitHub) and is accented as the
// terminal node. Multi-story inputs branch at the PO step (see caption below).
const STAGES = [
  "Analyst",
  "PO",
  "Story",
  "Acceptance",
  "Tests",
  "Review",
  "Backlog",
] as const;

export function Hero() {
  return (
    <section className="space-y-5 pt-2" data-testid="hero">
      <div className="space-y-3">
        <span className="inline-flex items-center rounded-full border border-border bg-muted/50 px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
          Multi-agent requirements pipeline
        </span>
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight sm:text-5xl">
          Raw requirements,{" "}
          <span className="bg-gradient-to-r from-primary to-amber-500 bg-clip-text text-transparent">
            one backlog-ready story.
          </span>
        </h1>
        <p className="max-w-xl text-base text-muted-foreground">
          Paste a feature request, PRD snippet, or meeting notes. RTIA returns a
          single user story with description, objective, acceptance criteria,
          and test cases — ready to drop into Jira or a GitHub backlog.
        </p>
      </div>

      <div className="space-y-2">
        <ol
          className="flex flex-wrap items-center gap-x-1.5 gap-y-2"
          aria-label="Pipeline stages"
          data-testid="hero-stages"
        >
          {STAGES.map((stage, i) => {
            const isTerminal = stage === "Backlog";
            return (
              <Fragment key={stage}>
                <li
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-xs font-medium shadow-sm",
                    isTerminal
                      ? "border-primary/30 bg-accent text-accent-foreground"
                      : "border-border bg-card text-foreground/80",
                  )}
                >
                  {stage}
                </li>
                {i < STAGES.length - 1 && (
                  <ArrowRight
                    className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50"
                    aria-hidden
                  />
                )}
              </Fragment>
            );
          })}
        </ol>
        <p className="text-xs text-muted-foreground">
          Multi-story requirements branch at the PO step into separate backlog
          stories; each can be re-run through the full pipeline on its own.
        </p>
      </div>
    </section>
  );
}
