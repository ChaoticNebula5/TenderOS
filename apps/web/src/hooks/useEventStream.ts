import { useEffect, useRef, useState } from "react";

import { getAccessToken } from "../api/client";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

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
// EventSource cannot send an Authorization header, so the access token
// travels as a query parameter instead (?access_token=...) - a real,
// accepted exposure (the token appears in server access logs / browser
// history for its 15-minute life), not fixed here. See
// KNOWN_LIMITATIONS.md's B14 SSE-auth entry for why, and for the token-
// expiry-mid-stream consequence this hook does NOT paper over: once the
// access token expires, EventSource's own reconnect attempt reuses the
// SAME (now-expired) URL, gets a fatal, non-retryable HTTP error from the
// server, and stops retrying on its own - readyState becomes CLOSED. This
// hook surfaces that as `error`, but nothing currently reads `error` from
// this hook's only consumer (UploadAndProcessingStatus.tsx destructures
// only `data`) - the same pre-existing gap usePolling's own error return
// already had (a throwing fetcher was equally invisible), now reachable
// via a much more ordinary trigger (a workflow run staying open past 15
// minutes) instead of requiring a fetch to literally throw.
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

    void refetch();

    const token = getAccessToken();
    const url = new URL(`${BASE_URL}/workflow-runs/${workflowRunId}/events/stream`);
    if (token !== null) url.searchParams.set("access_token", token);
    const source = new EventSource(url.toString());

    source.onmessage = () => {
      void refetch();
    };
    source.onerror = () => {
      if (!cancelled && source.readyState === EventSource.CLOSED) {
        setError(new Error("Workflow event stream closed"));
      }
    };

    return () => {
      cancelled = true;
      source.close();
    };
  }, [workflowRunId, enabled]);

  return { data, error };
}
