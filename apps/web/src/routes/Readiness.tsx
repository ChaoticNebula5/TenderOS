import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  generateReadiness,
  getReadiness,
  listGates,
  listWorkflowRuns,
} from "../api/client";
import type { GateResponse, ReadinessReportResponse } from "../api/client";
import { GateDecisionPanel } from "../components/GateDecisionPanel";
import { Button } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { StatusIcon } from "../components/ui/icons";
import { Notice } from "../components/ui/Notice";
import { formatDate } from "../format/date";
import { humanize } from "../format/humanize";
import { useReportRunStatus } from "../shell/runStatus";
import "./Readiness.css";

// The eleven readiness counts (_evaluate_readiness_once in readiness_service.py) in words.
// chosen, not in DESIGN: hand-written so each says what is counted - e.g.
// critical_major_redteam_findings counts open critical AND major findings
// (_OPEN_REDTEAM_SEVERITIES in readiness_service.py). A key not listed here falls back
// to humanize(), so a count the backend adds later still shows.
const COUNT_LABELS: Readonly<Record<string, string>> = {
  requirements_total: "Requirements",
  requirements_completed: "Requirements completed",
  mandatory_requirements_unresolved: "Mandatory requirements unresolved",
  compliance_decisions_awaiting_approval: "Compliance decisions awaiting approval",
  requirements_lacking_valid_evidence: "Compliant decisions without valid evidence",
  critical_major_redteam_findings: "Open critical or major red-team findings",
  missing_submission_artefacts: "Missing mandatory submission items",
  commercial_findings_open: "Open commercial findings",
  resource_gaps: "Roles without a selected candidate",
  proposal_claims_needing_review: "Proposal claims needing review",
  open_clarifications: "Open clarifications",
};

function countLabel(key: string): string {
  return Object.hasOwn(COUNT_LABELS, key) ? COUNT_LABELS[key] : humanize(key);
}

// The eight blocker categories (the constants atop readiness_service.py) in words.
// chosen, not in DESIGN (polish pass): hand-written like COUNT_LABELS, so
// "critical_redteam_finding" reads "Critical red-team finding" - as the nav
// says "Red team" - not humanize()'s "Critical redteam finding". A category
// the backend adds later falls back to humanize.
const BLOCKER_CATEGORY_LABELS: Readonly<Record<string, string>> = {
  unresolved_mandatory_requirement: "Unresolved mandatory requirement",
  compliant_decision_without_valid_evidence: "Compliant decision without valid evidence",
  critical_redteam_finding: "Critical red-team finding",
  missing_mandatory_submission_item: "Missing mandatory submission item",
  unapproved_required_commercial_item: "Unapproved required commercial item",
  role_with_no_selected_candidate: "Role without a selected candidate",
  stale_graph_projection: "Stale graph projection",
  red_team_review_incomplete: "Red-team review incomplete",
};

function blockerCategoryLabel(category: string): string {
  return Object.hasOwn(BLOCKER_CATEGORY_LABELS, category)
    ? BLOCKER_CATEGORY_LABELS[category]
    : humanize(category);
}

// Blockers with the same category and message, as one line with a count
// (polish pass): three distinct critical findings carry one identical
// message, and three identical lines read as a glitch. Order is the
// report's own, by first appearance.
interface BlockerGroup {
  category: string;
  message: string;
  count: number;
}

function groupBlockers(blockers: ReadinessReportResponse["blockers"]): BlockerGroup[] {
  const groups = new Map<string, BlockerGroup>();
  for (const b of blockers) {
    const key = `${b.category}\u0000${b.message}`;
    const group = groups.get(key);
    if (group) group.count += 1;
    else groups.set(key, { category: b.category, message: b.message, count: 1 });
  }
  return [...groups.values()];
}

function blockerCount(n: number): string {
  if (n === 0) return "no blockers";
  return `${n} ${n === 1 ? "blocker" : "blockers"}`;
}

// Screen 15 (Part J). Same "most recent workflow run -> its gate" lookup
// shape as screens 8/9/11/12, this time for the final_readiness gate -
// the Approver's actual decision point. The readiness report itself
// (counts/blockers) is fetched separately from GET /readiness, not
// derived from the gate - the gate only carries a ready/blockers
// snapshot as of when it opened; this screen shows the current report.
//
// Restyle batch 5 - which report is "current" (DESIGN.md Batch 5):
// GET /readiness on mount returns the latest STORED report
// (get_latest_readiness in readiness_service.py), which may predate later changes to the
// tender, and nothing this screen fetches says the tender's current state
// version. So a stored report is shown as exactly that - "Last stored
// report: state version N ..." - with no <strong> and never the word
// "Ready". Only a report generated in this visit (POST, computed now)
// gets the verdict: <strong>Ready</strong> or <strong>Not ready</strong>,
// text unchanged. full-flow.spec.ts (step 13, readiness) waits for that page's only
// <strong> (strict mode) and reads its exact text, so the verdict
// can only be read from a report the click just generated. Mount
// behaviour and fetch order are unchanged.
//
// :1454-1456 read the "Blockers" <h2> and its following-sibling <ul> of
// <li>, each holding the blocker message verbatim (step 13, readiness). The gate stays
// BELOW the report (a recorded deviation from DESIGN.md's Gates pattern):
// its request-time snapshot must not sit above the report being read.
export function Readiness() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [report, setReport] = useState<ReadinessReportResponse | null>(null);
  // True only for a report generated in this visit (handleGenerate).
  const [reportIsFresh, setReportIsFresh] = useState(false);
  // Set as soon as Generate succeeds, and reset when the tender changes.
  // The mount GET is not cancelled by a Generate click, and Generate is not
  // disabled while it is in flight; if Generate answers first, the late GET
  // must not overwrite the fresh report with the stored one (that would
  // drop the verdict <strong> the spec reads, full-flow.spec.ts (step 13, readiness)).
  const generatedThisVisit = useRef(false);
  // Set once GET /readiness has answered (a report or a 404), so the empty
  // state only claims "none yet" after the response says so.
  const [reportLoaded, setReportLoaded] = useState(false);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  // The latest run's status, from the listWorkflowRuns call this screen
  // already makes for the gate - reported to the tender bar. No extra fetch.
  const [runStatus, setRunStatus] = useState<string | null>(null);
  useReportRunStatus(runStatus);

  async function refreshReport() {
    if (!tenderId) return;
    try {
      const stored = await getReadiness(tenderId);
      if (generatedThisVisit.current) return;
      setReport(stored);
      setReportIsFresh(false);
    } catch (err) {
      if (generatedThisVisit.current) return;
      if (err instanceof ApiError && err.status === 404) {
        setReport(null);
      } else {
        throw err;
      }
    }
    setReportLoaded(true);
  }

  useEffect(() => {
    generatedThisVisit.current = false;
    setReportLoaded(false);
    refreshReport().catch(() => setError("Failed to load the readiness report"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenderId]);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setRunStatus(null);
    listWorkflowRuns(tenderId)
      .then(async (runs) => {
        if (cancelled || runs.length === 0) return;
        setRunStatus(runs[0].status);
        const gates = await listGates(runs[0].id);
        const readinessGate = gates.find((g) => g.gate_type === "final_readiness");
        if (!cancelled && readinessGate) setGate(readinessGate);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load the final readiness gate");
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  async function handleGenerate() {
    if (!tenderId) return;
    setError(null);
    setGenerating(true);
    try {
      const generated = await generateReadiness(tenderId);
      generatedThisVisit.current = true;
      setReport(generated);
      setReportIsFresh(true);
      setReportLoaded(true);
    } catch (err) {
      setError(err instanceof ApiError ? `Generate failed (${err.status})` : "Generate failed");
    } finally {
      setGenerating(false);
    }
  }

  // As the other gate screens: a decision moves the run on server-side and
  // this screen does not re-fetch it, so the badge goes blank, not stale.
  function handleGateDecided(updated: GateResponse) {
    setGate(updated);
    setRunStatus(null);
  }

  return (
    <main>
      <h1>Readiness</h1>

      {error && (
        <Notice variant="failed" alert>
          {error}
        </Notice>
      )}

      <div className="action-row readiness__actions">
        <Button
          variant="primary"
          onClick={handleGenerate}
          busy={generating}
          busyLabel="Generating..."
        >
          Generate readiness report
        </Button>
      </div>

      {reportLoaded && report === null && (
        <EmptyState title="No readiness report yet">
          Generate a report to check this tender against every readiness rule.
        </EmptyState>
      )}

      {report !== null && (
        <>
          {reportIsFresh ? (
            <div className="readiness__verdict">
              <p
                className={`readiness__verdict-line readiness__verdict-line--${report.ready ? "ready" : "blocked"}`}
              >
                <StatusIcon
                  tone={report.ready ? "ready" : "blocked"}
                  className="readiness__verdict-icon"
                />
                <strong>{report.ready ? "Ready" : "Not ready"}</strong>
              </p>
              <p className="readiness__meta">
                Report for state version {report.tender_state_version}, generated{" "}
                {formatDate(report.generated_at)}.
              </p>
            </div>
          ) : (
            <p className="readiness__stored">
              Last stored report: state version {report.tender_state_version}, generated{" "}
              {formatDate(report.generated_at)}, {blockerCount(report.blockers.length)} at that
              version. Generate a report to check the current state.
            </p>
          )}

          <h2>Blockers</h2>
          {report.blockers.length === 0 ? (
            <p className="readiness__none">No blockers.</p>
          ) : (
            // Polish pass (P7): one <li> per distinct blocker, its message
            // verbatim (step 13, readiness, looks for the D6 message inside an <li> and
            // needs at least one), and how many blockers it stands for.
            <ul className="readiness__blockers">
              {groupBlockers(report.blockers).map((group) => (
                <li key={`${group.category}:${group.message}`}>
                  <StatusIcon tone="blocked" className="readiness__blocker-icon" />
                  <span>
                    <span className="readiness__blocker-category">
                      {blockerCategoryLabel(group.category)}
                    </span>{" "}
                    {group.message}
                    {/* The space sits outside the count, so the line can still
                        break before it; the count itself never splits
                        (.readiness__blocker-count). textContent unchanged. */}
                    {group.count > 1 && " "}
                    {group.count > 1 && (
                      <span className="readiness__blocker-category readiness__blocker-count">
                        ({group.count} blockers)
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h2>Counts</h2>
          <dl className="readiness__counts">
            {Object.entries(report.counts).map(([key, value]) => (
              <div key={key}>
                <dt>{countLabel(key)}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </>
      )}

      <h2>Final readiness gate</h2>
      {gate ? (
        <GateDecisionPanel gate={gate} onDecided={handleGateDecided} />
      ) : (
        <p>No open final readiness gate for this tender yet.</p>
      )}
    </main>
  );
}
