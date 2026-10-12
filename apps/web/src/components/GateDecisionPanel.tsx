import { useState } from "react";
import type { GateResponse } from "../api/client";
import { ApiError, decideGate } from "../api/client";
import { retryTransient } from "../api/retryTransient";
import { formatDate } from "../format/date";
import { describeReadiness, describeStatus } from "../status/statusMap";
import { Button } from "./ui/Button";
import type { ButtonVariant } from "./ui/buttonClassName";
import { Field } from "./ui/Field";
import { Notice } from "./ui/Notice";
import { StatusBadge } from "./ui/StatusBadge";
import { StatusIcon } from "./ui/icons";
import "./GateDecisionPanel.css";

interface GateDecisionPanelProps {
  gate: GateResponse;
  onDecided: (updated: GateResponse) => void;
}

type Decision = "approve" | "reject" | "request_changes";

// Restyle batch 3 (DESIGN.md Components, "Gate panel"): one action
// vocabulary for every gate. Approve is the region's primary action,
// Request changes secondary, Reject danger (a rejected gate fails the run).
const DECISIONS: readonly (readonly [Decision, string, ButtonVariant])[] = [
  ["approve", "Approve", "primary"],
  ["request_changes", "Request changes", "secondary"],
  ["reject", "Reject", "danger"],
];

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
  // The decision in flight, or null. All three buttons are disabled while
  // one is submitting (as before); only the clicked one is aria-busy. No
  // "Approving..." participle: none existed before, and a restyle does not
  // invent copy (DESIGN.md Buttons, busy state).
  const [pendingDecision, setPendingDecision] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const submitting = pendingDecision !== null;

  async function handleDecide(decision: Decision) {
    setError(null);
    setPendingDecision(decision);
    // Generated once, outside retryTransient's own loop, and reused across
    // every retry of THIS decision - the backend's idempotency middleware
    // (apps/api/src/tenderos_api/idempotency.py) keys a stored response on
    // (organization_id, operation, idempotency_key) and replays it on a
    // repeat rather than re-running the decision. A fresh key per attempt
    // would defeat that: a first attempt that actually succeeded server-
    // side but timed out on the response would let a retry re-decide the
    // gate under a new key instead of safely replaying the first outcome.
    const idempotencyKey = crypto.randomUUID();
    try {
      const updated = await retryTransient(() =>
        decideGate(
          gate.gate_id,
          {
            decision,
            notes: notes.trim() === "" ? null : notes,
            waive: gate.gate_type === "final_readiness" ? waive : false,
          },
          idempotencyKey,
        ),
      );
      onDecided(updated);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("This gate's readiness is stale or unresolved blockers block approval.");
      } else {
        setError("Failed to record the decision.");
      }
    } finally {
      setPendingDecision(null);
    }
  }

  // The panel's root stays a <section>: every gate screen renders its <h2>
  // and then this <section> as siblings, and full-flow.spec.ts:56 finds the
  // panel that way (following-sibling::section[1]). Nothing may wrap it.
  //
  // Header: the gate's own status as a badge, decided or not, then the
  // summary labelled as the snapshot it is - the counts are fixed when the
  // gate is requested and do not follow later edits (DESIGN.md Gate panel,
  // "Summary honesty").
  const header = (
    <>
      <div className="gate-panel__header">
        <StatusBadge {...describeStatus("gate", gate.status)} />
      </div>
      <p className="gate-panel__asof">At request time · {formatDate(gate.requested_at)}</p>
      <GateSummary gate={gate} />
    </>
  );

  if (gate.status !== "pending") {
    // Decided: the badge above plus when, in the shared date format. No
    // <strong> (full-flow.spec.ts:1451 is a strict-mode check on the
    // readiness page's only <strong>, and this panel renders there too),
    // no raw ISO string, and no "who": decided_by is a user id, and a name
    // would need a fetch a restyle does not add.
    return (
      <section className="gate-panel">
        {header}
        {gate.decided_at && (
          <p className="gate-panel__decided">Decided {formatDate(gate.decided_at)}</p>
        )}
      </section>
    );
  }

  const needsWaiver = gate.gate_type === "final_readiness" && !gate.summary.ready;

  return (
    <section className="gate-panel">
      {header}
      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}
      {needsWaiver && (
        <label className="gate-panel__waive">
          <input type="checkbox" checked={waive} onChange={(e) => setWaive(e.target.checked)} />
          Waive outstanding blockers
        </label>
      )}
      <Field label="Notes (optional)">
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} />
      </Field>
      <div className="action-row">
        {DECISIONS
          // gate.allowed_decisions is always all three today -
          // orchestration/gates.py's ALLOWED_DECISIONS is one fixed
          // tuple for every gate_type, not yet varied per type - but the
          // field exists on the schema specifically to allow that later,
          // and rendering a decision the backend would reject is a
          // silent dead end for whoever clicks it.
          .filter(([decision]) => gate.allowed_decisions.includes(decision))
          .map(([decision, label, variant]) => (
            <Button
              key={decision}
              variant={variant}
              onClick={() => handleDecide(decision)}
              disabled={submitting}
              busy={pendingDecision === decision}
            >
              {label}
            </Button>
          ))}
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
//
// A definition grid (DESIGN.md Gate panel), not a <ul>: each count is a
// term and a value, and the values are tabular. No E2E locator reads these
// counts, and the pages whose spec lines count <li> page-wide (proposal
// :971/:974, knowledge selections :1253/:1258) render no gate panel
// (measured before this change: 0 panels on either page).
function GateSummary({ gate }: { gate: GateResponse }) {
  switch (gate.gate_type) {
    case "requirement_review":
      return (
        <SummaryGrid
          items={[
            ["Requirements", gate.summary.requirement_count],
            ["Mandatory", gate.summary.mandatory_count],
            ["Unverified mandatory", gate.summary.unverified_mandatory_count],
            ["Submission items", gate.summary.submission_item_count],
            ["Clarifications", gate.summary.clarification_count],
          ]}
        />
      );
    case "compliance_review":
      return (
        <SummaryGrid
          items={[
            ["Compliance entries", gate.summary.compliance_entry_count],
            ["Pending approval", gate.summary.pending_count],
            ["High risk", gate.summary.high_risk_count],
          ]}
        />
      );
    case "resource_selection":
      return (
        <SummaryGrid
          items={[
            ["Roles", gate.summary.role_count],
            ["Candidates", gate.summary.candidate_count],
            ["Unselected roles", gate.summary.unselected_count],
          ]}
        />
      );
    case "commercial_approval":
      return (
        <SummaryGrid
          items={[
            ["Findings", gate.summary.finding_count],
            ["Pending", gate.summary.pending_count],
            ["Critical", gate.summary.critical_count],
          ]}
        />
      );
    case "finding_resolution":
      return (
        <SummaryGrid
          items={[
            ["Open findings", gate.summary.open_finding_count],
            ["Critical", gate.summary.critical_count],
          ]}
        />
      );
    case "final_readiness":
      // The readiness verdict - the one place "Ready" may appear
      // (describeReadiness). Blockers stay a list, each with the blocked
      // icon. This <ul> sits inside the panel's <section>, so it is never
      // the "Blockers" <h2>'s following sibling that full-flow.spec.ts:1455
      // reads on the readiness page (that <ul> is Readiness.tsx's own).
      return (
        <div className="gate-summary__readiness">
          <StatusBadge {...describeReadiness(gate.summary.ready)} />
          {gate.summary.blockers.length > 0 && (
            <ul className="gate-blockers">
              {gate.summary.blockers.map((blocker, i) => (
                <li key={i}>
                  <StatusIcon tone="blocked" className="gate-blockers__icon" />
                  {blocker.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      );
  }
}

function SummaryGrid({ items }: { items: [string, number][] }) {
  return (
    <dl className="gate-summary">
      {items.map(([term, value]) => (
        <div key={term} className="gate-summary__item">
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
