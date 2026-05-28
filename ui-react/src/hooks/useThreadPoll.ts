import { useEffect, useRef, useState } from "react";

import { ApiError, getThreadState } from "@/lib/api";
import type { ThreadState, ThreadStatus } from "@/lib/types";

const POLL_INTERVAL_MS = 2_000;
const BACKOFF_BASE_MS = 5_000;
const BACKOFF_MAX_MS = 30_000;
const TERMINAL_STATUSES: ThreadStatus[] = ["done", "done_split", "error"];

export type PollPhase =
  | "idle"
  | "polling"
  | "auth_required"
  | "stopped"
  | "transient_error";

export interface UseThreadPollResult {
  state: ThreadState | null;
  phase: PollPhase;
  /** Last 5xx / network error, surfaced for display while we back off. */
  lastError: string | null;
  /** True once status hits DONE / DONE_SPLIT / ERROR. */
  isTerminal: boolean;
}

/**
 * Poll ``GET /pipeline/{threadId}`` every ~2 s while the thread is active.
 *
 * Behaviours required by US-18:
 *
 * - Polls every {@link POLL_INTERVAL_MS} until a terminal status, then stops.
 * - Pauses when the tab is hidden ("visibilitychange") and resumes on focus,
 *   without queueing up duplicate calls (Edge AC).
 * - 401 / 403 → switches to ``auth_required`` and stops polling instead of
 *   retrying silently (Negative AC).
 * - 5xx / network failure → exponential backoff capped at
 *   {@link BACKOFF_MAX_MS}, error surfaced via ``lastError`` (Negative AC).
 * - One in-flight request at a time (guarded by ``inFlight``) so a slow
 *   request can't fan out duplicates when the interval fires again.
 */
export function useThreadPoll(threadId: string | null): UseThreadPollResult {
  const [state, setState] = useState<ThreadState | null>(null);
  const [phase, setPhase] = useState<PollPhase>("idle");
  const [lastError, setLastError] = useState<string | null>(null);

  // Refs the running interval reads without re-triggering the effect.
  const inFlightRef = useRef(false);
  const consecutiveFailuresRef = useRef(0);
  const isTerminalRef = useRef(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!threadId) {
      setState(null);
      setPhase("idle");
      setLastError(null);
      isTerminalRef.current = false;
      consecutiveFailuresRef.current = 0;
      return;
    }

    let cancelled = false;
    setPhase("polling");
    setLastError(null);
    isTerminalRef.current = false;
    consecutiveFailuresRef.current = 0;

    const clearPending = () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };

    const schedule = (delay: number) => {
      clearPending();
      if (cancelled) return;
      timeoutRef.current = setTimeout(tick, delay);
    };

    const tick = async () => {
      if (cancelled) return;
      // Don't fire while tab is hidden — resume runs on visibilitychange.
      if (document.visibilityState === "hidden") return;
      // Don't fan out a second request when the previous is still pending.
      if (inFlightRef.current) {
        schedule(POLL_INTERVAL_MS);
        return;
      }
      inFlightRef.current = true;
      try {
        const next = await getThreadState(threadId);
        if (cancelled) return;
        setState(next);
        setLastError(null);
        consecutiveFailuresRef.current = 0;
        if (TERMINAL_STATUSES.includes(next.status)) {
          isTerminalRef.current = true;
          setPhase("stopped");
          return;
        }
        setPhase("polling");
        schedule(POLL_INTERVAL_MS);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
          // Stop. Re-running won't help — the user needs a fresh token.
          setPhase("auth_required");
          setLastError(e.message);
          return;
        }
        consecutiveFailuresRef.current += 1;
        const delay = Math.min(
          BACKOFF_BASE_MS * 2 ** (consecutiveFailuresRef.current - 1),
          BACKOFF_MAX_MS,
        );
        setPhase("transient_error");
        setLastError(e instanceof Error ? e.message : "Polling failed");
        schedule(delay);
      } finally {
        inFlightRef.current = false;
      }
    };

    const onVisibilityChange = () => {
      if (cancelled || isTerminalRef.current) return;
      if (document.visibilityState === "visible") {
        // Tab regained focus — fire immediately so the badge doesn't sit
        // on stale state, but don't queue duplicates if a request is
        // somehow still in flight (the tick guard handles that too).
        schedule(0);
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    // Kick off immediately so the badge updates without a 2 s blank gap.
    schedule(0);

    return () => {
      cancelled = true;
      clearPending();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [threadId]);

  return {
    state,
    phase,
    lastError,
    isTerminal: isTerminalRef.current,
  };
}
