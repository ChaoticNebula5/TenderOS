import { useState } from "react";
import type { GateResponse } from "../api/client";
import { ApiError, decideGate } from "../api/client";
import { formatErrorDetail } from "../api/formatErrorDetail";
import { retryTransient } from "../api/retryTransient";
import { formatDate } from "../format/date";
import { formatUsd } from "../format/usd";
import { describeReadiness, describeStatus } from "../status/statusMap";
import type { StatusDescriptor } from "../status/statusMap";
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
  // Plan item 73: one sentence on why the run paused here and what deciding
  // does, shown while the gate is pending (the finding-resolution and
  // cost-budget gates, whose screens have no table explaining them).
  description?: string;
}

type Decision = "approve" | "reject" | "request_changes";

// Plan item 72: a cancel closes the run's pending gate as "rejected" with
// decision_payload {"decision": null, "closed_by": "run_cancelled"}
// (db/repositories/gates.py, CLOSED_BY_CANCEL_PAYLOAD). Nobody rejected
// it, so it reads "Closed by cancel", Neutral like a cancelled run.
function isClosedByCancel(gate: GateResponse): boolean {
  return gate.decision_payload?.closed_by === "run_cancelled";
}

function describeGate(gate: GateResponse): StatusDescriptor {
  return isClosedByCancel(gate)
    ? { tone: "neutral", label: "Closed by cancel" }
    : describeStatus("gate", gate.status);
}

// Plan item 71: a decided gate shows what the reviewer wrote, and whether
// an approval waived outstanding readiness blockers (routes/gates.py
// records both in decision_payload). Before, a decided panel showed only
// its badge and date, so a Request changes' reason and a waiver were
// invisible after the fact.
function decisionNotes(gate: GateResponse): string | null {
  const notes = gate.decision_payload?.notes;
  return typeof notes === "string" && notes.trim() !== "" ? notes : null;
}

// The `code` from the API's {"error": {"code", "message"}} envelope, or null
// for any other body shape.
function errorCode(err: ApiError): string | null {
  const body = err.body;
  if (typeof body !== "object" || body === null || !("error" in body)) return null;
  const error = (body as { error: unknown }).error;
  if (typeof error !== "object" || error === null || !("code" in error)) return null;
  const code = (error as { code: unknown }).code;
  return typeof code === "string" ? code : null;
}

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
export function GateDecisionPanel({ gate, onDecided, description }: GateDecisionPanelProps) {
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
        // Phase 4 blocker 2: a 409 from this route is not always about
        // readiness. Only READINESS_NOT_WAIVED is (errors.py's
        // ReadinessNotWaivedError, raised by routes/gates.py's decide_gate
        // for a final_readiness approval with unresolved or stale blockers
        // and no waiver). The others - IDEMPOTENCY_IN_PROGRESS,
        // IDEMPOTENCY_CONFLICT, GATE_ALREADY_DECIDED - carry their own
        // message in the API's error envelope, shown as is.
        setError(
          errorCode(err) === "READINESS_NOT_WAIVED"
            ? "This gate's readiness is stale or unresolved blockers block approval."
            : formatErrorDetail(err),
        );
      } else {
        setError("Failed to record the decision.");
      }
    } finally {
      setPendingDecision(null);
    }
  }

  // The panel's root stays a <section>: every gate screen renders its <h2>
  // and then this <section> as siblings, and full-flow.spec.ts (gatePanel()) finds the
  // panel that way (following-sibling::section[1]). Nothing may wrap it.
  //
  // Header: the gate's own status as a badge, decided or not, then its
  // counts. Plan item 73, decision 3 (user, 2026-11-13): a pending gate
  // shows its counts as they are now first (live_summary, which the screen
  // re-reads after each row change, gates/refreshGate.ts), then the
  // snapshot taken when the gate opened, labelled so. When the two agree,
  // one line says so instead of a second identical grid. A decided gate
  // shows only its snapshot: the record of what was decided on. Before,
  // the snapshot alone sat beside live tables ("Pending approval 2" next to
  // two approved rows; DESIGN.md "Summary honesty").
  const openedAt = formatDate(gate.requested_at);
  const live = liveItems(gate);
  const snapshot = summaryItems(gate, "snapshot");
  const header = (
    <>
      <div className="gate-panel__header">
        <StatusBadge {...describeGate(gate)} />
      </div>
      {live !== null ? (
        <>
          <p className="gate-panel__asof">Now</p>
          <SummaryGrid items={live} />
          {snapshot !== null && sameItems(live, snapshot) ? (
            <p className="gate-panel__asof gate-panel__asof--unchanged">
              Unchanged since this gate opened · {openedAt}
            </p>
          ) : (
            <>
              <p className="gate-panel__asof">When this gate opened · {openedAt}</p>
              <GateSummary gate={gate} />
            </>
          )}
        </>
      ) : (
        <>
          <p className="gate-panel__asof">When this gate opened · {openedAt}</p>
          <GateSummary gate={gate} />
        </>
      )}
    </>
  );

  if (gate.status !== "pending") {
    // Decided: the badge above plus when, in the shared date format. No
    // <strong> (full-flow.spec.ts (step 13, readiness) is a strict-mode check on the
    // readiness page's only <strong>, and this panel renders there too),
    // no raw ISO string, and no "who": decided_by is a user id, and a name
    // would need a fetch a restyle does not add.
    const notes = decisionNotes(gate);
    return (
      <section className="gate-panel">
        {header}
        {gate.decided_at && (
          <p className="gate-panel__decided">
            {isClosedByCancel(gate) ? "Closed" : "Decided"} {formatDate(gate.decided_at)}
          </p>
        )}
        {gate.decision_payload?.waived === true && (
          <p className="gate-panel__waived">Approved with outstanding blockers waived.</p>
        )}
        {notes !== null && (
          <p className="gate-panel__notes">
            <span className="gate-panel__notes-label">Reviewer&apos;s notes:</span> {notes}
          </p>
        )}
      </section>
    );
  }

  const needsWaiver = gate.gate_type === "final_readiness" && !gate.summary.ready;

  return (
    <section className="gate-panel">
      {header}
      {description && <p className="gate-panel__description">{description}</p>}
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

type SummaryItem = [term: string, value: string];

// The count gates' terms and values, from the snapshot or the live counts.
// Switches on gate_type - the same discriminant Pydantic uses on the
// backend - so TypeScript narrows `summary` to the matching interface in
// each branch. Each branch names its own real fields (from schemas/gates.py)
// rather than iterating Object.entries generically, since a reader benefits
// from seeing "unverified_mandatory_count" as a real label, not a
// formatted-from-a-key guess. null for final_readiness, which is not a list
// of counts, and for live counts the response does not carry.
function summaryItems(gate: GateResponse, which: "snapshot" | "live"): SummaryItem[] | null {
  const pick = <T,>(snapshot: T, live: T | null | undefined): T | null =>
    which === "snapshot" ? snapshot : (live ?? null);
  switch (gate.gate_type) {
    case "requirement_review": {
      const c = pick(gate.summary, gate.live_summary);
      return (
        c && [
          ["Requirements", String(c.requirement_count)],
          ["Mandatory", String(c.mandatory_count)],
          ["Unverified mandatory", String(c.unverified_mandatory_count)],
          ["Submission items", String(c.submission_item_count)],
          ["Clarifications", String(c.clarification_count)],
        ]
      );
    }
    case "compliance_review": {
      const c = pick(gate.summary, gate.live_summary);
      return (
        c && [
          ["Compliance entries", String(c.compliance_entry_count)],
          ["Pending approval", String(c.pending_count)],
          ["High risk", String(c.high_risk_count)],
        ]
      );
    }
    case "resource_selection": {
      // Plan item 73 (item 79's C3): candidate_count counts (role,
      // candidate) matches, so it is "Candidate matches", and the
      // unselected figure is roles with no pick. The old unselected_count
      // (matches) is no longer shown; a snapshot from before
      // unselected_role_count existed shows no such line.
      const c = pick(gate.summary, gate.live_summary);
      if (c === null) return null;
      const items: SummaryItem[] = [
        ["Roles", String(c.role_count)],
        ["Candidate matches", String(c.candidate_count)],
      ];
      if (typeof c.unselected_role_count === "number") {
        items.push(["Roles without a pick", String(c.unselected_role_count)]);
      }
      return items;
    }
    case "commercial_approval": {
      const c = pick(gate.summary, gate.live_summary);
      return (
        c && [
          ["Findings", String(c.finding_count)],
          ["Pending", String(c.pending_count)],
          ["Critical", String(c.critical_count)],
        ]
      );
    }
    case "finding_resolution": {
      const c = pick(gate.summary, gate.live_summary);
      return (
        c && [
          ["Open findings", String(c.open_finding_count)],
          ["Critical", String(c.critical_count)],
        ]
      );
    }
    case "cost_budget": {
      // Plan item 73: this run's LLM spend against its budget, in USD
      // (format/usd.ts); not a count of objects like the gates above.
      const c = pick(gate.summary, gate.live_summary);
      return (
        c && [
          ["Spent", formatUsd(c.accumulated_cost_usd)],
          ["Budget", formatUsd(c.budget_usd)],
          ["Over budget by", formatUsd(c.overage_usd)],
        ]
      );
    }
    case "final_readiness":
      return null;
  }
}

// A pending gate's live counts, or null to show the snapshot alone.
function liveItems(gate: GateResponse): SummaryItem[] | null {
  return gate.status === "pending" ? summaryItems(gate, "live") : null;
}

function sameItems(a: SummaryItem[], b: SummaryItem[]): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// The snapshot taken when the gate opened. A definition grid (DESIGN.md
// Gate panel), not a <ul>: each count is a term and a value, and the
// values are tabular. No E2E locator reads these counts, and the pages
// whose spec lines count <li> page-wide (proposal :971/:974, knowledge
// selections :1253/:1258) render no gate panel.
function GateSummary({ gate }: { gate: GateResponse }) {
  if (gate.gate_type === "final_readiness") {
    // The readiness verdict - the one place "Ready" may appear
    // (describeReadiness). Blockers stay a list, each with the blocked
    // icon. This <ul> sits inside the panel's <section>, so it is never
    // the "Blockers" <h2>'s following sibling that full-flow.spec.ts (step 13, readiness)
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
  return <SummaryGrid items={summaryItems(gate, "snapshot") ?? []} />;
}

function SummaryGrid({ items }: { items: SummaryItem[] }) {
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
