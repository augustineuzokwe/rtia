import { useEffect, useState } from "react";

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

interface PoCheckpointDeepProps {
  threadId: string;
  /** ``payload.critical_ambiguities`` from the paused thread state. */
  questions: string[];
  /** Called with the post-resume thread state so the parent can swap views. */
  onResumed: (next: ThreadState) => void;
}

/**
 * Deep-mode PO checkpoint. Renders one Textarea per critical ambiguity
 * the Analyst flagged, and POSTs the answers back as ``{question: answer}``.
 * Empty answers are fine — that mirrors the Gradio behaviour: skipping a
 * question becomes an unanswered assumption the Story Writer falls back
 * on, surfaced again at the Story Review checkpoint.
 */
export function PoCheckpointDeep({
  threadId,
  questions,
  onResumed,
}: PoCheckpointDeepProps) {
  // Per-question answer text. Indexed by question string because that's the
  // shape the API expects on the way out.
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Reset local form state when the question set changes (defensive — the
  // checkpoint won't re-pause on the same thread, but if the parent
  // remounts with a new thread the old draft shouldn't leak across).
  useEffect(() => {
    setAnswers({});
    setError(null);
  }, [threadId, questions.join("|")]);

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      // Build the dict the server wants: only include questions the PO
      // actually answered. Empty strings mean "no answer" — pass them
      // through so the assumption set surfaces them downstream.
      const payload: Record<string, string> = {};
      for (const q of questions) {
        payload[q] = answers[q] ?? "";
      }
      const next = await resumeThread(threadId, { answers: payload });
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

  // Defensive: a thread could theoretically pause at PO with zero critical
  // ambiguities (analyst returned nothing critical but the checkpoint
  // node still ran). Submitting an empty answers dict resumes cleanly.
  const hasQuestions = questions.length > 0;

  return (
    <Card data-testid="po-checkpoint-deep">
      <CardHeader>
        <CardTitle>PO checkpoint</CardTitle>
        <CardDescription>
          {hasQuestions
            ? "Answer the critical ambiguities the Analyst flagged, then resume the pipeline. Leave a field blank to record it as an assumption."
            : "No critical ambiguities were flagged. Resume to continue the pipeline."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {questions.map((q, i) => {
          const id = `po-answer-${i}`;
          return (
            <div key={q} className="space-y-2" data-testid={`po-question-${i}`}>
              <Label htmlFor={id} className="text-sm font-medium leading-snug">
                {q}
              </Label>
              <Textarea
                id={id}
                data-testid={`po-answer-${i}`}
                placeholder="Your answer (leave blank to skip)…"
                value={answers[q] ?? ""}
                onChange={(e) =>
                  setAnswers((prev) => ({ ...prev, [q]: e.target.value }))
                }
                rows={3}
                disabled={submitting}
              />
            </div>
          );
        })}

        {error && (
          <Alert
            variant="destructive"
            role="alert"
            data-testid="po-error"
          >
            <AlertTitle>Resume failed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="flex justify-end">
          <Button
            onClick={onSubmit}
            disabled={submitting}
            data-testid="po-submit"
          >
            {submitting ? "Resuming…" : "Submit answers"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
