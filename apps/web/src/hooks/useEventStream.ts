import { useEffect, useRef, useState } from "react";

import { ApiError, mintWorkflowEventsTicket } from "../api/client";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const _MAX_MINT_RETRIES = 3;

// B14. Same { data, error } contract usePolling.ts already promised this
// swap would preserve - callers destructure the return value identically,
// only the import changes. fetcher is reused exactly as usePolling used
// it: called once on mount, then again on every real SSE message, so the
// actual data always comes from the normal REST route (listWorkflowTasks/
// getWorkflowRun), never from the stream's own payload directly. That's
// deliberate, not a shortcut - GET .../events/stream sends G13's
// metadata-only wake-up pings (event_type/workflow_run_id/task_id/
// task_type/timestamp, no task content), so there is nothing richer in
// the stream to read; re-fetching is the only correct source for current
// state, matching the backend's own "re-query, don't trust the payload"
// design (KNOWN_LIMITATIONS.md's B14 SSE-transport entry).
//
// Honesty requirement carried from the backend: GET .../events/stream is
// itself server-side polling of the outbox at ~1s, re-emitted as a push
// stream - not database-level push. This hook is a thin EventSource
// wrapper around that; it doesn't change what "live" actually means here.
//
// B15: EventSource still cannot send an Authorization header, but the raw
// 15-minute access token no longer travels in the URL at all - see
// sse_tickets.py's own module docstring and KNOWN_LIMITATIONS.md's B14/B15
// SSE-auth entries. This hook now mints a short-lived, single-use ticket
// (mintWorkflowEventsTicket) before every connection attempt, including
// reconnects, and opens EventSource with that instead.
//
// This makes the old token-expiry-mid-stream gap this hook used to
// document MORE pressing, not less: because a ticket is single-use,
// EventSource's own automatic reconnect (which always replays the exact
// same URL after any drop, not just after 15 minutes) would now fail on
// EVERY reconnect, not just an occasional one past the token's lifetime.
// openStream below handles this actively: on a terminal failure
// (readyState CLOSED - the connection failed in a way EventSource itself
// won't retry, e.g. the 401 a reused/expired ticket gets), it mints a
// fresh ticket and opens a new connection itself, rather than relying on
// EventSource's own reconnect logic at all. Minting failure is split by
// cause: a 401 means the user's own session/access token has expired -
// retrying would get the identical rejection forever, so this surfaces as
// `error` and stops; anything else (a network hiccup, the API momentarily
// unreachable) gets a bounded retry with backoff, the same shape
// gemini_adapter.py's own _call_with_retry uses server-side.
export function useEventStream<T>(
  fetcher: () => Promise<T>,
  workflowRunId: string,
  enabled: boolean,
): { data: T | null; error: unknown } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let source: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;

    async function refetch() {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err);
      }
    }

    async function openStream(mintAttempt: number): Promise<void> {
      if (cancelled) return;

      let ticket: string;
      try {
        ticket = (await mintWorkflowEventsTicket(workflowRunId)).ticket;
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          setError(err);
          return;
        }
        if (mintAttempt >= _MAX_MINT_RETRIES) {
          setError(err);
          return;
        }
        retryTimeout = setTimeout(
          () => void openStream(mintAttempt + 1),
          1000 * 2 ** mintAttempt,
        );
        return;
      }
      if (cancelled) return;

      void refetch();

      const url = new URL(`${BASE_URL}/workflow-runs/${workflowRunId}/events/stream`);
      url.searchParams.set("ticket", ticket);
      source = new EventSource(url.toString());

      source.onmessage = () => {
        void refetch();
      };
      source.onerror = () => {
        if (cancelled || source === null) return;
        if (source.readyState === EventSource.CLOSED) {
          source.close();
          void openStream(0);
        }
      };
    }

    void openStream(0);

    return () => {
      cancelled = true;
      if (retryTimeout !== null) clearTimeout(retryTimeout);
      source?.close();
    };
  }, [workflowRunId, enabled]);

  return { data, error };
}
