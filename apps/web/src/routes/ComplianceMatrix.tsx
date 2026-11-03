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
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { Notice } from "../components/ui/Notice";
import { StatusBadge } from "../components/ui/StatusBadge";
import { TableScroll } from "../components/ui/TableScroll";
import { formatPercent } from "../format/percent";
import { useReportRunStatus } from "../shell/runStatus";
import { describeApproved, describeStatus } from "../status/statusMap";
import "./ComplianceMatrix.css";

// The override select's choices, in the order the screen has always listed
// them - db/models/compliance.py:53-56. Each <option>'s VALUE stays the
// backend word: full-flow.spec.ts (step 6, compliance) compares select.inputValue() against
// "fully_compliant" and step 6, compliance calls selectOption("fully_compliant"), which
// matches by value. Only the visible text goes through the label map
// (DESIGN.md "The No Raw Enum Rule").
const COMPLIANCE_STATUSES = [
  "fully_compliant",
  "partially_compliant",
  "non_compliant",
  "clarification_required",
  "evidence_unavailable",
  "not_reviewed",
] as const;

// Screen 9 (Part J). Same gate-lookup shape as RequirementRegister
// (screen 8): most recent workflow run -> its compliance_review gate.
// Per-entry approve/override goes through this module's own dedicated
// routes, never through the gate's target_updates - see
// GateDecisionPanel's own design note.
export function ComplianceMatrix() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [entries, setEntries] = useState<ComplianceResponse[]>([]);
  // Set once listCompliance answers, so the empty state only claims "no
  // entries" after the response says so (DESIGN.md Copy rules).
  const [entriesLoaded, setEntriesLoaded] = useState(false);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The latest run's status, from the listWorkflowRuns call this screen
  // already makes for the gate - reported to the tender bar. No extra fetch.
  const [runStatus, setRunStatus] = useState<string | null>(null);
  useReportRunStatus(runStatus);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setEntriesLoaded(false);
    listCompliance(tenderId)
      .then((result) => {
        if (cancelled) return;
        setEntries(result);
        setEntriesLoaded(true);
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
    // A tender change keeps this component mounted; the previous tender's
    // run status must not show against the new one while it loads.
    setRunStatus(null);
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        setRunStatus(runs[0].status);
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

  // A decision moves the run on server-side, but this screen does not
  // re-fetch the run, so the badge goes blank until the next load rather
  // than show a status the decision just made stale (as RequirementRegister).
  function handleGateDecided(updated: GateResponse) {
    setGate(updated);
    setRunStatus(null);
  }

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

  const approvedCount = entries.filter((e) => e.human_approved).length;

  // Heading and panel always render together, as siblings:
  // full-flow.spec.ts (gatePanel()) finds the panel as the heading's following
  // <section>. Above the table while pending (DESIGN.md Screen patterns,
  // "Gates"), below it once decided or not yet open. The panel holds no
  // <table>, so the matrix stays page.locator("table").first() (step 6, compliance).
  const gateBlock = (
    <>
      <h2>Compliance review gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={handleGateDecided} />
      ) : (
        <p>No open compliance review gate for this tender yet.</p>
      )}
    </>
  );
  const gateFirst = gate?.status === "pending";

  return (
    <main>
      <h1>Compliance matrix</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      {gateFirst && gateBlock}

      {entries.length > 0 && (
        <p className="count-line">
          {entries.length} {entries.length === 1 ? "entry" : "entries"}, {approvedCount} approved
        </p>
      )}

      {/* E2E contract (full-flow.spec.ts (step 6, compliance)): this is the page's first
          table; tbody holds only data rows (no skeleton or empty-state row -
          step 6, compliance counts them as "loaded"); the first cell is the requirement id
          (step 6, compliance); each row has one <select> whose option values are backend
          words (step 6, compliance); the row's Approve button is REMOVED, not
          disabled, once approved (step 6, compliance wait for it to hide). */}
      {/* No stickyLastColumn (measured at 390px, batch 4): pinning the
          actions column left less room between the two sticky columns than
          the Status column (badge plus select) needs, so it could never be
          seen whole. The actions scroll instead. */}
      <TableScroll label="Compliance entries">
        <table className="compliance" aria-busy={!entriesLoaded || undefined}>
          <thead>
            <tr>
              <th>Requirement</th>
              <th>Status</th>
              <th className="cell-num">Confidence</th>
              <th>Approved</th>
              <th>
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.id}>
                {/* The requirement's display code needs listRequirements, a
                    fetch this screen does not make (DESIGN.md Tables). */}
                <td className="cell-id">{entry.requirement_id}</td>
                <td>
                  <div className="compliance__status">
                    <StatusBadge {...describeStatus("compliance", entry.status)} />
                    <select
                      aria-label="Change status"
                      value={entry.status}
                      onChange={(e) => handleOverride(entry.id, e.target.value)}
                    >
                      {COMPLIANCE_STATUSES.map((value) => (
                        <option key={value} value={value}>
                          {describeStatus("compliance", value).label}
                        </option>
                      ))}
                    </select>
                  </div>
                </td>
                <td className="cell-num">{formatPercent(entry.confidence) ?? "Not scored"}</td>
                <td>
                  <StatusBadge {...describeApproved(entry.human_approved)} />
                </td>
                <td>
                  {!entry.human_approved && (
                    <Button variant="quiet" onClick={() => handleApprove(entry.id)}>
                      Approve
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>

      {entriesLoaded && entries.length === 0 && (
        <EmptyState title="No compliance entries yet">
          Entries appear here when compliance analysis runs, after the requirement review gate is
          approved.
        </EmptyState>
      )}

      {!gateFirst && gateBlock}
    </main>
  );
}
