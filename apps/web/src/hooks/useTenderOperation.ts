import { useEffect, useRef, useState } from "react";

import { getActiveOperation, isFinishedOperation } from "../api/client";
import type { OperationKind, OperationResponse } from "../api/client";
import { useOperation } from "./useOperation";

// Phase 4 Stage 2. One screen's button for one kind of async operation on
// one tender (Run red team, Generate proposal, Re-validate), built on
// useOperation. It adds the three things every such screen needs:
//   1. start(post): runs the screen's POST, then follows the operation it
//      returns (the POST answers 202 at once; the worker does the work);
//   2. reattach: on load it asks GET /tenders/{id}/operations/active for
//      this kind and, if one is queued or running, follows it exactly as
//      after a click, so a reload does not lose track of a run;
//   3. settle: once the operation is finished, it awaits the screen's
//      onFinished (re-read the results, show a failure) BEFORE busy goes
//      false, so the button is never enabled while the screen still shows
//      the old results (full-flow.spec.ts (step 9, proposal) waits for the button to be
//      enabled, then expects a section already visible).
// busy is true from the click until that settle has finished.
export function useTenderOperation(
  tenderId: string | undefined,
  kind: OperationKind,
  onFinished: (operation: OperationResponse) => Promise<void>,
): {
  busy: boolean;
  start: (post: () => Promise<OperationResponse>) => Promise<void>;
} {
  // `starting` covers the POST itself, before there is an id to follow.
  const [starting, setStarting] = useState(false);
  // The operation being followed, with the tender it belongs to: an id kept
  // from a previous tender is ignored rather than reset in an effect.
  const [tracked, setTracked] = useState<{ tenderId: string; operationId: string } | null>(null);
  // The id whose onFinished has completed; busy ends for it.
  const [settledId, setSettledId] = useState<string | null>(null);
  const operationId =
    tracked !== null && tracked.tenderId === tenderId ? tracked.operationId : null;
  const { operation } = useOperation(operationId);

  // Always the latest render's callback, so onFinished re-reads with the
  // screen's current state, without restarting the effect below.
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;
  const tenderIdRef = useRef(tenderId);
  tenderIdRef.current = tenderId;

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    getActiveOperation(tenderId, kind)
      .then((active) => {
        if (cancelled || active === null) return;
        // A click that already started (or joined) an operation wins: the
        // server returns the same active one to both anyway.
        setTracked((prev) =>
          prev !== null && prev.tenderId === tenderId
            ? prev
            : { tenderId, operationId: active.operation_id },
        );
      })
      .catch(() => {
        // Best effort. Without it the screen behaves as before a reload: the
        // button is enabled, and a click returns the same active operation,
        // so no second run starts.
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId, kind]);

  const finished =
    operation !== null && operation.operation_id === operationId && isFinishedOperation(operation)
      ? operation
      : null;
  const finishedId = finished?.operation_id ?? null;
  useEffect(() => {
    if (finished === null) return;
    let cancelled = false;
    const id = finished.operation_id;
    onFinishedRef
      .current(finished)
      // A failed re-read is the screen's to report; it must not keep the
      // button busy for good.
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setSettledId(id);
      });
    return () => {
      cancelled = true;
    };
    // Once per finished operation: its status cannot change after that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedId]);

  async function start(post: () => Promise<OperationResponse>): Promise<void> {
    const startedFor = tenderId;
    if (!startedFor) return;
    setStarting(true);
    try {
      const started = await post();
      // Skipped if the user moved to another tender while the POST was out.
      if (tenderIdRef.current === startedFor) {
        setTracked({ tenderId: startedFor, operationId: started.operation_id });
      }
    } finally {
      setStarting(false);
    }
  }

  const busy = starting || (operationId !== null && settledId !== operationId);
  return { busy, start };
}
