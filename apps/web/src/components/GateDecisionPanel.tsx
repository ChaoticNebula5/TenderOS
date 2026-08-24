import { useState } from "react";
import type { GateResponse } from "../api/client";
import { ApiError, decideGate } from "../api/client";

interface GateDecisionPanelProps {
  gate: GateResponse;
  onDecided: (updated: GateResponse) => void;
}

// The "shared" part of the shared gate-control component (B13 §7 step 5,
// TODO(human) 1) is deliberately narrow: deciding the gate as a whole
// (approve/reject/request_changes, notes, final_readiness's waive flag)
// and showing its typed summary. It does NOT render per-target checkboxes
// - reading gate_target_service.py directly (not the schema, which says
// nothing about this) turned up a real asymmetry that makes one generic
// "pick your targets" UI the wrong shape to share:
//
//   - compliance_review's targets are compliance_entry ids, and
//     gate_target_service._KNOWN_TARGET_TYPES accepts compliance_entry
//     target_updates directly - the gate's own targets list IS what a
//     caller can batch-approve.
//   - resource_selection's targets are role_requirement ids (one per
//     role), but _KNOWN_TARGET_TYPES only accepts role_match target_
//     updates (one per CANDIDATE) - a different id space entirely, only
//     reachable via a separate GET /roles/{role_id}/matches call per
//     role. The gate's targets list can't be turned into target_updates
//     by itself.
//   - requirement_review's targets (requirement ids) and finding_
//     resolution's (redteam_finding ids) aren't in _KNOWN_TARGET_TYPES at
//     all - per-item approval for both happens through their own existing
//     routes (POST /requirements/{id}/verify, POST /redteam-findings/
//     {id}/resolve), never through this gate's target_updates.
//
// Three different shapes for "what does per-target approval mean here,"
// one of which doesn't exist. A single shared component trying to render
// all three would need gate_type-specific branches anyway - so those
// branches live in each SCREEN instead (screen 9's compliance table,
// screen 11's team workspace), each already building the per-row UI its
// own domain needs. A screen collects whatever target_updates it wants
// and passes them in via decideTargetUpdates; this component only ever
// asks "what's the decision on the gate overall."
export function GateDecisionPanel({ gate, onDecided }: GateDecisionPanelProps) {
  const [notes, setNotes] = useState("");
  const [waive, setWaive] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDecide(decision: "approve" | "reject" | "request_changes") {
    setError(null);
    setSubmitting(true);
    try {
      const updated = await decideGate(
        gate.gate_id,
        {
          decision,
          notes: notes.trim() === "" ? null : notes,
          waive: gate.gate_type === "final_readiness" ? waive : false,
        },
        crypto.randomUUID(),
      );
      onDecided(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("This gate's readiness is stale or unresolved blockers block approval.");
      } else {
        setError("Failed to record the decision.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (gate.status !== "pending") {
    return (
      <section>
        <GateSummary gate={gate} />
        <p>
          Decision: <strong>{gate.status}</strong>
          {gate.decided_at && ` on ${gate.decided_at}`}
        </p>
      </section>
    );
  }

  const needsWaiver = gate.gate_type === "final_readiness" && !gate.summary.ready;

  return (
    <section>
      <GateSummary gate={gate} />
      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
      {needsWaiver && (
        <label>
          <input type="checkbox" checked={waive} onChange={(e) => setWaive(e.target.checked)} />
          Waive outstanding blockers
        </label>
      )}
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes (optional)"
      />
      <div>
        <button type="button" onClick={() => handleDecide("approve")} disabled={submitting}>
          Approve
        </button>
        <button type="button" onClick={() => handleDecide("request_changes")} disabled={submitting}>
          Request changes
        </button>
        <button type="button" onClick={() => handleDecide("reject")} disabled={submitting}>
          Reject
        </button>
      </div>
    </section>
  );
}

// Switches on gate_type - the same discriminant Pydantic uses on the
// backend - so TypeScript narrows `gate.summary` to the matching interface
// in each branch instead of the union. Each branch names its own real
// fields (from schemas/gates.py's now-typed summaries) rather than
// iterating Object.entries(gate.summary) generically, since a reader
// benefits from seeing "unverified_mandatory_count" as a real label, not
// a formatted-from-a-key guess.
function GateSummary({ gate }: { gate: GateResponse }) {
  switch (gate.gate_type) {
    case "requirement_review":
      return (
        <ul>
          <li>Requirements: {gate.summary.requirement_count}</li>
          <li>Mandatory: {gate.summary.mandatory_count}</li>
          <li>Unverified mandatory: {gate.summary.unverified_mandatory_count}</li>
          <li>Submission items: {gate.summary.submission_item_count}</li>
          <li>Clarifications: {gate.summary.clarification_count}</li>
        </ul>
      );
    case "compliance_review":
      return (
        <ul>
          <li>Compliance entries: {gate.summary.compliance_entry_count}</li>
          <li>Pending approval: {gate.summary.pending_count}</li>
          <li>High risk: {gate.summary.high_risk_count}</li>
        </ul>
      );
    case "resource_selection":
      return (
        <ul>
          <li>Roles: {gate.summary.role_count}</li>
          <li>Candidates: {gate.summary.candidate_count}</li>
          <li>Unselected roles: {gate.summary.unselected_count}</li>
        </ul>
      );
    case "commercial_approval":
      return (
        <ul>
          <li>Findings: {gate.summary.finding_count}</li>
          <li>Pending: {gate.summary.pending_count}</li>
          <li>Critical: {gate.summary.critical_count}</li>
        </ul>
      );
    case "finding_resolution":
      return (
        <ul>
          <li>Open findings: {gate.summary.open_finding_count}</li>
          <li>Critical: {gate.summary.critical_count}</li>
        </ul>
      );
    case "final_readiness":
      return (
        <div>
          <p>Ready: {gate.summary.ready ? "yes" : "no"}</p>
          {gate.summary.blockers.length > 0 && (
            <ul>
              {gate.summary.blockers.map((blocker, i) => (
                <li key={i}>{blocker.message}</li>
              ))}
            </ul>
          )}
        </div>
      );
  }
}
