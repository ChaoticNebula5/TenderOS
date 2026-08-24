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
    // The requirement_review gate lives on a workflow run, not the
    // tender directly - runs are newest-first (listWorkflowRuns' own
    // ordering), so the most recent run is the one whose gate matters.
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
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

  return (
    <main>
      <h1>Requirement register</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>Code</th>
            <th>Requirement</th>
            <th>Category</th>
            <th>Mandatory</th>
            <th>Verified</th>
            <th>Reviewed</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {requirements.map((r) => (
            <tr key={r.id} style={r.rejected_at ? { opacity: 0.5 } : undefined}>
              <td>{r.display_code}</td>
              <td>{r.requirement_text}</td>
              <td>{r.category}</td>
              <td>{r.mandatory ? "yes" : "no"}</td>
              <td>{r.verified ? "yes" : "no"}</td>
              <td>{r.human_reviewed ? "yes" : "no"}</td>
              <td>
                {!r.rejected_at && (
                  <>
                    <button type="button" onClick={() => handleConfirm(r.id)}>
                      Confirm
                    </button>
                    <button type="button" onClick={() => handleReject(r.id)}>
                      Reject
                    </button>
                    <button type="button" onClick={() => handleVerify(r.id)}>
                      Re-verify
                    </button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Requirement review gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={setGate} />
      ) : (
        <p>No open requirement review gate for this tender yet.</p>
      )}
    </main>
  );
}
