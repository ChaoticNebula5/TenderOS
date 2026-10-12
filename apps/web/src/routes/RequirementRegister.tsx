import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import type { GateResponse, RequirementResponse } from "../api/client";
import {
  ApiError,
  listGates,
  listRequirements,
  listWorkflowRuns,
  patchRequirement,
  verifyRequirement,
} from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";
import { Button } from "../components/ui/Button";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { useReportRunStatus } from "../shell/runStatus";
import { describeVerified } from "../status/statusMap";
import "./RequirementRegister.css";

// Screen 8 (Part J). Routes: GET /tenders/{id}/requirements (G5, already
// fully covered by tests/integration/test_requirements_api.py - no new
// route tests needed for this screen, per the corrected §3a inventory)
// plus GET /workflow-runs/{id}/gates (G4, this step's own new coverage).
// merge/split (G5) have no UI here yet - real routes, no screen consumes
// them (§3a's own inventory marks both "screen-only," not E2E-scenario-
// reached), left for a later pass rather than built speculatively now.
export function RequirementRegister() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [requirements, setRequirements] = useState<RequirementResponse[]>([]);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The latest run's status, from the listWorkflowRuns call below that this
  // screen already makes for the gate - kept so the tender bar can show it
  // (useReportRunStatus). No extra fetch. null = not known (no run yet, or
  // invalidated by a gate decision below), which shows no badge.
  const [runStatus, setRunStatus] = useState<string | null>(null);
  useReportRunStatus(runStatus);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    listRequirements(tenderId)
      .then((result) => {
        if (!cancelled) setRequirements(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load requirements");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    // A tender change keeps this component mounted; the previous tender's
    // run status must not show against the new one while it loads.
    setRunStatus(null);
    // The requirement_review gate lives on a workflow run, not the
    // tender directly - runs are newest-first (listWorkflowRuns' own
    // ordering), so the most recent run is the one whose gate matters.
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        setRunStatus(runs[0].status);
        const gates = await listGates(runs[0].id);
        const requirementGate = gates.find((g) => g.gate_type === "requirement_review");
        if (!cancelled && requirementGate) setGate(requirementGate);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the requirement review gate");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  // A decision moves the run on server-side, but this screen does not
  // re-fetch the run. Rather than keep showing a status the decision just
  // made stale, the badge goes blank until the next load.
  function handleGateDecided(updated: GateResponse) {
    setGate(updated);
    setRunStatus(null);
  }

  async function handleConfirm(requirementId: string) {
    setError(null);
    try {
      const updated = await patchRequirement(requirementId, { action: "confirm" });
      setRequirements((prev) => prev.map((r) => (r.id === requirementId ? updated : r)));
    } catch (err) {
      setError(err instanceof ApiError ? `Confirm failed (${err.status})` : "Confirm failed");
    }
  }

  async function handleReject(requirementId: string) {
    setError(null);
    try {
      const updated = await patchRequirement(requirementId, { action: "reject" });
      setRequirements((prev) => prev.map((r) => (r.id === requirementId ? updated : r)));
    } catch (err) {
      setError(err instanceof ApiError ? `Reject failed (${err.status})` : "Reject failed");
    }
  }

  async function handleVerify(requirementId: string) {
    setError(null);
    try {
      // 202: the real re-check runs on the worker - this response is the
      // requirement's state at request time, not the verified one yet.
      // Re-fetching the list is what actually shows the outcome.
      await verifyRequirement(requirementId);
      if (tenderId) setRequirements(await listRequirements(tenderId));
    } catch (err) {
      setError(err instanceof ApiError ? `Verify failed (${err.status})` : "Verify failed");
    }
  }

  const unverifiedMandatory = requirements.filter(
    (r) => r.mandatory && !r.verified && !r.rejected_at,
  ).length;

  // The gate heading and its panel always render together, as siblings:
  // full-flow.spec.ts:56 finds the panel as the heading's following
  // <section>. While the gate is pending the pair sits above the table - the
  // decision an operator came for is the first thing on screen (DESIGN.md
  // Screen patterns, "Gates") - and below it once decided or not yet open.
  const gateBlock = (
    <>
      <h2>Requirement review gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={handleGateDecided} />
      ) : (
        <p>No open requirement review gate for this tender yet.</p>
      )}
    </>
  );
  const gateFirst = gate?.status === "pending";

  return (
    <main>
      <h1>Requirement register</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      {gateFirst && gateBlock}

      {requirements.length > 0 && (
        <p className="count-line">
          {requirements.length} {requirements.length === 1 ? "requirement" : "requirements"},{" "}
          {unverifiedMandatory} unverified mandatory
        </p>
      )}

      {/* Column order is fixed by the E2E: full-flow.spec.ts:653 reads the
          sixth cell (td index 5, Reviewed) and expects exactly "yes". So no
          column is added before it, and Reviewed stays a plain word even
          though it is a state (DESIGN.md Status badge would make it a
          badge) - a spec-forced deviation. Mandatory is a fact, a plain word
          by DESIGN.md's own rule. Only Verified becomes a badge. No skeleton
          rows: :629-638 waits for this table's first <tr> as its "data has
          loaded" signal, which placeholder rows would satisfy early. */}
      <TableScroll label="Requirements" stickyLastColumn>
        <table className="requirements">
          <thead>
            <tr>
              <th>Code</th>
              <th>Requirement</th>
              <th>Category</th>
              <th>Mandatory</th>
              <th>Verified</th>
              <th>Reviewed</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {requirements.map((r) => (
              <tr key={r.id} className={r.rejected_at ? "requirements__row--rejected" : undefined}>
                <td>{r.display_code}</td>
                <td>{r.requirement_text}</td>
                <td>{r.category}</td>
                <td>{r.mandatory ? "yes" : "no"}</td>
                <td>
                  <StatusBadge {...describeVerified(r.verified, r.mandatory)} />
                </td>
                <td>{r.human_reviewed ? "yes" : "no"}</td>
                <td>
                  {!r.rejected_at && (
                    <div className="action-row">
                      <Button variant="quiet" onClick={() => handleConfirm(r.id)}>
                        Confirm
                      </Button>
                      <Button variant="danger" onClick={() => handleReject(r.id)}>
                        Reject
                      </Button>
                      <Button variant="quiet" onClick={() => handleVerify(r.id)}>
                        Re-verify
                      </Button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>

      {!gateFirst && gateBlock}
    </main>
  );
}
