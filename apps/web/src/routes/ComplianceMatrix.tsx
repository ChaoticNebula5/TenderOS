import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  approveCompliance,
  listCompliance,
  listGates,
  listWorkflowRuns,
  patchCompliance,
} from "../api/client";
import type { ComplianceResponse, GateResponse } from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";

// Screen 9 (Part J). Same gate-lookup shape as RequirementRegister
// (screen 8): most recent workflow run -> its compliance_review gate.
// Per-entry approve/override goes through this module's own dedicated
// routes, never through the gate's target_updates - see
// GateDecisionPanel's own design note.
export function ComplianceMatrix() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [entries, setEntries] = useState<ComplianceResponse[]>([]);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    listCompliance(tenderId)
      .then((result) => {
        if (!cancelled) setEntries(result);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load compliance entries");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        const gates = await listGates(runs[0].id);
        const complianceGate = gates.find((g) => g.gate_type === "compliance_review");
        if (!cancelled && complianceGate) setGate(complianceGate);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the compliance review gate");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  async function handleApprove(complianceId: string) {
    setError(null);
    try {
      const updated = await approveCompliance(complianceId);
      setEntries((prev) => prev.map((e) => (e.id === complianceId ? updated : e)));
    } catch (err) {
      setError(err instanceof ApiError ? `Approve failed (${err.status})` : "Approve failed");
    }
  }

  async function handleOverride(complianceId: string, status: string) {
    setError(null);
    try {
      const updated = await patchCompliance(complianceId, {
        status,
        reasoning_summary: "Overridden by reviewer.",
      });
      setEntries((prev) => prev.map((e) => (e.id === complianceId ? updated : e)));
    } catch (err) {
      setError(err instanceof ApiError ? `Override failed (${err.status})` : "Override failed");
    }
  }

  return (
    <main>
      <h1>Compliance matrix</h1>

      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}

      <table>
        <thead>
          <tr>
            <th>Requirement</th>
            <th>Status</th>
            <th>Confidence</th>
            <th>Approved</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id}>
              <td>{entry.requirement_id}</td>
              <td>
                <select
                  value={entry.status}
                  onChange={(e) => handleOverride(entry.id, e.target.value)}
                >
                  <option value="fully_compliant">fully_compliant</option>
                  <option value="partially_compliant">partially_compliant</option>
                  <option value="non_compliant">non_compliant</option>
                  <option value="clarification_required">clarification_required</option>
                  <option value="evidence_unavailable">evidence_unavailable</option>
                  <option value="not_reviewed">not_reviewed</option>
                </select>
              </td>
              <td>{entry.confidence ?? "-"}</td>
              <td>{entry.human_approved ? "yes" : "no"}</td>
              <td>
                {!entry.human_approved && (
                  <button type="button" onClick={() => handleApprove(entry.id)}>
                    Approve
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Compliance review gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={setGate} />
      ) : (
        <p>No open compliance review gate for this tender yet.</p>
      )}
    </main>
  );
}
