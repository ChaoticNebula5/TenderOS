import { useEffect, useRef, useState } from "react";

import { ApiError, mintOperationEventsTicket, mintWorkflowEventsTicket } from "../api/client";

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
// gemini_adapter.py's own _call_with_retry uses server-side. A terminal
// EventSource failure AFTER a successful mint+open (source.onerror with
// readyState CLOSED) gets the identical exponential-backoff treatment,
// via its own streamFailures counter reset only on a real onopen - the
// original version reconnected those with openStream(0) directly, no
// delay and no cap, which hammers the mint endpoint in a tight loop
// whenever the stream itself (not just minting) keeps failing the same
// way.
// Phase 4: what a stream follows. A workflow run's progress (B14) and an
// async operation's status use the same transport; only the ticket route
// and the stream path differ.
export type EventStreamTarget = { kind: "workflowRun" | "operation"; id: string };

const STREAM_ROUTES = {
  workflowRun: {
    mintTicket: mintWorkflowEventsTicket,
    path: (id: string) => `/workflow-runs/${id}/events/stream`,
  },
  operation: {
    mintTicket: mintOperationEventsTicket,
    path: (id: string) => `/operations/${id}/events/stream`,
  },
} as const;

// The workflow-run form every existing caller uses; unchanged behaviour.
export function useEventStream<T>(
  fetcher: () => Promise<T>,
  workflowRunId: string,
  enabled: boolean,
): { data: T | null; error: unknown; streamDown: boolean } {
  return useEventStreamFor(fetcher, { kind: "workflowRun", id: workflowRunId }, enabled);
}

export function useEventStreamFor<T>(
  fetcher: () => Promise<T>,
  target: EventStreamTarget,
  enabled: boolean,
): { data: T | null; error: unknown; streamDown: boolean } {
  const { kind: targetKind, id: targetId } = target;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  // Phase 4: true while there is no live stream (a ticket could not be
  // minted, or the connection closed and is backing off), false once a
  // connection opens. `error` only reports the failures this hook gives up
  // on; a caller that must not wait on a reconnecting stream (useOperation's
  // polling fallback) reads this instead.
  const [streamDown, setStreamDown] = useState(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let source: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;
    // Separate from mintAttempt: a terminal stream failure (readyState
    // CLOSED - e.g. the ticket got consumed by a duplicate tab, or the
    // server dropped the connection) used to call openStream(0) directly,
    // with zero delay and no cap. Every reconnect that failed the same way
    // immediately retried, hammering the ticket-mint endpoint in a tight
    // loop. Reset only on a real onopen, so a connection that succeeds and
    // later drops again still backs off from zero, not from wherever the
    // last failure streak left off.
    let streamFailures = 0;
    const _MAX_STREAM_BACKOFF_MS = 30_000;

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
        ticket = (await STREAM_ROUTES[targetKind].mintTicket(targetId)).ticket;
      } catch (err) {
        if (cancelled) return;
        setStreamDown(true);
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

      const url = new URL(`${BASE_URL}${STREAM_ROUTES[targetKind].path(targetId)}`);
      url.searchParams.set("ticket", ticket);
      source = new EventSource(url.toString());

      source.onopen = () => {
        streamFailures = 0;
        setStreamDown(false);
      };
      source.onmessage = () => {
        void refetch();
      };
      source.onerror = () => {
        if (cancelled || source === null) return;
        if (source.readyState === EventSource.CLOSED) {
          source.close();
          setStreamDown(true);
          const delay = Math.min(1000 * 2 ** streamFailures, _MAX_STREAM_BACKOFF_MS);
          streamFailures += 1;
          retryTimeout = setTimeout(() => void openStream(0), delay);
        }
      };
    }

    void openStream(0);

    return () => {
      cancelled = true;
      if (retryTimeout !== null) clearTimeout(retryTimeout);
      source?.close();
    };
  }, [targetKind, targetId, enabled]);

  return { data, error, streamDown };
}
