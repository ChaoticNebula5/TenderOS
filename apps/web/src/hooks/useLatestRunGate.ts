import { useCallback, useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { getWorkflowRun, listGates, listWorkflowRuns } from "../api/client";
import type { GateResponse, GateType, WorkflowRunResponse } from "../api/client";
import { isActiveRunStatus } from "../status/runStatuses";
import { useEventStream } from "./useEventStream";

// Plan item 78: a gate screen's gate (Requirements, Compliance, Team,
// Commercial, Red team, Readiness), kept current without a reload. Each of
// those screens had its own copy of one mount-only effect: the tender's
// newest run (listWorkflowRuns is newest-first), that run's gates, the
// first one of the screen's type. A gate that opened after the screen
// loaded (observed in the item 66 walk: "No open resource selection gate"
// until a reload) never showed.
//
// Now, while that run is active, the screen follows the run's event stream
// (B14's, the same one the Documents page follows) and reads the run and
// its gates again on every message. A gate opening moves the run to
// waiting_for_human, which writes WorkflowRunStatusChanged, so the message
// arrives as the gate opens. Once the run ends, the stream closes. The
// stream sends nothing but wake-ups (G13); the gate always comes from the
// REST read.
//
// The first of the screen's type is kept, as before: each of these six
// gates opens at most once per run (its node reuses the run's gate by key,
// and a revision pass skips the post-join gates, routing.py). Only the
// cost-budget gate can open again, and the Documents page shows that one.
//
// A screen's own rows (requirements, compliance entries, roles, pricing
// findings, red-team findings, the readiness report) are written by the
// stage that runs just before its gate opens. So when a stream read finds
// the screen's gate newly opened (none shown before, or a different one),
// `onGateOpened` lets the screen read its rows again. Observed on the live
// stack before this was added: the compliance gate appeared saying
// "Compliance entries 2" above a table saying "No compliance entries yet".
// Not on the first load, which reads the rows and the gate together.
//
// The tender bar (shell/TenderLayout.tsx) still opens no stream: the frozen
// spec counts the Documents page's stream requests (full-flow.spec.ts,
// step 8's live tab), and every tender screen sits inside the bar.

interface RunAndGates {
  run: WorkflowRunResponse;
  gates: GateResponse[];
  // How many decisions this screen had made when the read started.
  decisionsAtStart: number;
}

export interface LatestRunGate {
  gate: GateResponse | null;
  // For refreshPendingGate (gates/refreshGate.ts): a pending gate's live
  // counts after a change on the screen; it applies only to a gate still
  // pending, so it cannot undo a decision either.
  setGate: Dispatch<SetStateAction<GateResponse | null>>;
  // The run's status for the tender bar's badge (useReportRunStatus); null
  // between a decision here and the first read after it.
  runStatus: string | null;
  // The screen's GateDecisionPanel onDecided.
  onDecided: (updated: GateResponse) => void;
}

export function useLatestRunGate(
  tenderId: string | undefined,
  gateType: GateType,
  onLoadError: () => void,
  onGateOpened?: () => void,
): LatestRunGate {
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [gate, setGate] = useState<GateResponse | null>(null);
  // The badge is hidden from a decision here until a read made after it.
  const [badgeHidden, setBadgeHidden] = useState(false);
  const decisions = useRef(0);
  const onLoadErrorRef = useRef(onLoadError);
  onLoadErrorRef.current = onLoadError;
  const onGateOpenedRef = useRef(onGateOpened);
  onGateOpenedRef.current = onGateOpened;
  // The id of the gate on screen, for telling a newly opened gate from a
  // fresher read of the same one.
  const shownGateId = useRef<string | null>(null);
  shownGateId.current = gate?.gate_id ?? null;

  const pickGate = useCallback(
    (gates: GateResponse[]) => gates.find((g) => g.gate_type === gateType) ?? null,
    [gateType],
  );

  useEffect(() => {
    // A tender change keeps the screen mounted: nothing of the previous
    // tender's run may show (or be decided) while this one loads.
    setRunId(null);
    setRunStatus(null);
    setGate(null);
    setBadgeHidden(false);
    if (!tenderId) return;
    let cancelled = false;
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        const latest = runs[0];
        const gates = await listGates(latest.id);
        if (cancelled) return;
        setRunId(latest.id);
        setRunStatus(latest.status);
        setGate(pickGate(gates));
      })
      .catch(() => {
        if (!cancelled) onLoadErrorRef.current();
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId, pickGate]);

  const following = runId !== null && runStatus !== null && isActiveRunStatus(runStatus);
  const { data: latest } = useEventStream<RunAndGates>(
    async () => {
      const decisionsAtStart = decisions.current;
      const id = runId as string;
      const [run, gates] = await Promise.all([getWorkflowRun(id), listGates(id)]);
      return { run, gates, decisionsAtStart };
    },
    runId ?? "",
    following,
  );

  useEffect(() => {
    // A read that started before this screen's latest decision can answer
    // after it, still showing the gate pending: applying it would offer the
    // decision again. useEventStream orders reads against each other, not
    // against a decision, so those are dropped here.
    if (latest === null || latest.decisionsAtStart < decisions.current) return;
    if (latest.run.id !== runId) return;
    setRunStatus(latest.run.status);
    setBadgeHidden(false);
    const found = pickGate(latest.gates);
    if (found === null) return;
    const opened = found.gate_id !== shownGateId.current;
    shownGateId.current = found.gate_id;
    setGate(found);
    if (opened) onGateOpenedRef.current?.();
  }, [latest, runId, pickGate]);

  const onDecided = useCallback((updated: GateResponse) => {
    decisions.current += 1;
    setGate(updated);
    // The decision moves the run on server-side; its stream's next message
    // brings the new status. Until then the badge shows nothing rather than
    // the status the decision just made stale.
    setBadgeHidden(true);
  }, []);

  return { gate, setGate, runStatus: badgeHidden ? null : runStatus, onDecided };
}
