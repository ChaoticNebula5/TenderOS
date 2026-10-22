import { useEffect, useState } from "react";

import { getOperation, isFinishedOperation } from "../api/client";
import type { OperationResponse } from "../api/client";
import { useEventStreamFor } from "./useEventStream";

// Phase 4. Follows one async operation (a red-team run started from this
// screen) until it is finished, so the screen can keep its button busy for
// exactly that long. Two sources:
//   1. the operation's SSE stream (useEventStreamFor): every status the
//      operation reaches pushes a wake-up, and the hook re-reads
//      GET /operations/{id};
//   2. polling GET /operations/{id} every POLL_INTERVAL_MS, only while the
//      stream is down (ticket refused, connection closed and backing off),
//      so a screen never waits on a stream that cannot connect.
// Both stop once the operation is finished (succeeded or failed).
export const POLL_INTERVAL_MS = 3000;

export function useOperation(operationId: string | null): {
  operation: OperationResponse | null;
  error: unknown;
} {
  const [polled, setPolled] = useState<OperationResponse | null>(null);
  const [pollError, setPollError] = useState<unknown>(null);
  // The id whose finished status has been seen; following stops for it, which
  // closes the stream (useEventStreamFor's cleanup) and ends any polling.
  const [doneId, setDoneId] = useState<string | null>(null);
  const following = operationId !== null && doneId !== operationId;

  const {
    data: streamed,
    error: streamError,
    streamDown,
  } = useEventStreamFor(
    () => getOperation(operationId as string),
    { kind: "operation", id: operationId ?? "" },
    following,
  );

  const operation = pickLatest(streamed, polled, operationId);
  const finished = operation !== null && isFinishedOperation(operation);

  useEffect(() => {
    if (finished && operationId !== null) setDoneId(operationId);
  }, [finished, operationId]);

  useEffect(() => {
    setPollError(null);
  }, [operationId]);

  const needsPolling = following && (streamDown || streamError !== null);
  useEffect(() => {
    if (!needsPolling || operationId === null) return;
    let cancelled = false;
    async function poll() {
      try {
        const result = await getOperation(operationId as string);
        if (!cancelled) {
          setPolled(result);
          setPollError(null);
        }
      } catch (err) {
        if (!cancelled) setPollError(err);
      }
    }
    void poll();
    const interval = setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [needsPolling, operationId]);

  // Only report an error when neither source has said anything.
  const error = operation === null ? (pollError ?? streamError) : null;
  return { operation, error };
}

// The stream and the poller answer independently, so an older "running"
// read can land after a newer "succeeded" one. Status only moves forward
// (queued, running, then succeeded or failed; the server's guarded updates
// enforce that), so the furthest status is the newest. Reads left over from
// a previous operation id are ignored.
function pickLatest(
  a: OperationResponse | null,
  b: OperationResponse | null,
  operationId: string | null,
): OperationResponse | null {
  const rank = { queued: 0, running: 1, succeeded: 2, failed: 2 } as const;
  const candidates = [a, b].filter(
    (o): o is OperationResponse => o !== null && o.operation_id === operationId,
  );
  if (candidates.length === 0) return null;
  return candidates.reduce((x, y) => (rank[y.status] > rank[x.status] ? y : x));
}
