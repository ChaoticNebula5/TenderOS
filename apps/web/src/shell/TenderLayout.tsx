import { useEffect, useState } from "react";
import { Link, NavLink, useLocation, useParams } from "react-router-dom";
import { getTender, listGates, listWorkflowRuns } from "../api/client";
import type { GateType } from "../api/client";
import { StatusBadge } from "../components/ui/StatusBadge";
import { GATE_SCREENS, gateScreenPath } from "../gates/gateScreens";
import { describeStatus } from "../status/statusMap";
import { PageContent } from "./AppShell";
import { RunStatusContext } from "./runStatus";
import "./TenderLayout.css";

// The workflow nav for every /tenders/:tenderId screen (DESIGN.md Layout,
// "Workflow nav", decision 7): a horizontal tab strip in pipeline order,
// Graph as a utility at the end, scrolling on its own at 390px. Plain
// links; NavLink sets aria-current="page" on the current step itself.
//
// Run status badge (batch 3): the tender bar shows the latest run's status,
// as the current screen reports it through useReportRunStatus
// (runStatus.ts) or, since plan item 74, as the bar's own read finds it.
// With neither, no badge: an unknown status is left blank, not guessed. It
// is a <span> (StatusBadge), never <strong>: the readiness page's E2E
// asserts on its only <strong> (full-flow.spec.ts (step 13, readiness)).
//
// Plan item 74 (minimal orientation, user decision 2026-11-13; text and a
// link only, the full redesign is item 66). The bar now reads three things
// itself, once per tender and again on every step change:
// - the bid's name (getTender), as plain text: no heading, link or
//   <strong> (the shell's own tests forbid them in the bar; the spec's one
//   link named after the bid is on the dashboard);
// - the latest run and, when it waits at a gate or ended with changes
//   requested, which gate, as "at <gate>" linking to the gate's screen
//   (gates/gateScreens.ts). Before, "Waiting for review" never said for
//   what, and screens showed no status at all between reports.
// A screen's reported status (useReportRunStatus) still wins while it is
// mounted: it comes from that screen's own fresher read. The bar opens no
// event stream of its own: full-flow.spec.ts counts the Documents page's
// stream requests (:994-1020), so it re-reads on navigation instead.
//
// Links sit directly in <nav>, no <ul>/<li>. The bar renders before the
// screen's own <main> (inside <PageContent /> below), so the spec's `main li` and
// `main ul li` (step 14, knowledge selections) never reach it; keeping it
// free of lists is still the simpler rule.

const STEPS = [
  { path: "", label: "Documents" },
  { path: "requirements", label: "Requirements" },
  { path: "compliance", label: "Compliance" },
  { path: "team", label: "Team" },
  { path: "commercial", label: "Commercial" },
  { path: "knowledge-selections", label: "Knowledge" },
  { path: "proposal", label: "Proposal" },
  { path: "redteam", label: "Red team" },
  { path: "readiness", label: "Readiness" },
] as const;

interface LatestRun {
  status: string;
  // The gate it waits at (waiting_for_human), or whose Request changes
  // ended it (changes_requested); null otherwise.
  gateType: GateType | null;
}

// A status whose gate the bar names: the gate's status to look for.
const GATE_FOR_STATUS: Readonly<Record<string, string>> = {
  waiting_for_human: "pending",
  changes_requested: "changes_requested",
};

async function readLatestRun(tenderId: string): Promise<LatestRun | null> {
  const runs = await listWorkflowRuns(tenderId);
  if (runs.length === 0) return null;
  const run = runs[0];
  const gateStatus = GATE_FOR_STATUS[run.status];
  if (gateStatus === undefined) return { status: run.status, gateType: null };
  const gates = await listGates(run.id);
  const gate = gates.filter((g) => g.status === gateStatus).at(-1);
  return { status: run.status, gateType: gate?.gate_type ?? null };
}

export function TenderLayout() {
  const { tenderId } = useParams();
  const base = `/tenders/${tenderId}`;
  const { pathname } = useLocation();
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [tenderName, setTenderName] = useState<string | null>(null);
  const [latestRun, setLatestRun] = useState<LatestRun | null>(null);

  useEffect(() => {
    setTenderName(null);
    setLatestRun(null);
    if (!tenderId) return;
    let cancelled = false;
    getTender(tenderId)
      .then((tender) => {
        if (!cancelled) setTenderName(tender.name);
      })
      .catch(() => {
        // No name shown; the steps still work.
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  // Re-read on every step change, and when a screen reports a different
  // status (a decision just moved the run on).
  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    readLatestRun(tenderId)
      .then((latest) => {
        if (!cancelled) setLatestRun(latest);
      })
      .catch(() => {
        if (!cancelled) setLatestRun(null);
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId, pathname, runStatus]);

  const shownStatus = runStatus ?? latestRun?.status ?? null;
  // Only the bar's own read knows the gate; it is named only while that
  // read agrees with the status shown.
  const atGate = latestRun !== null && latestRun.status === shownStatus ? latestRun.gateType : null;

  return (
    <RunStatusContext.Provider value={setRunStatus}>
      <div className="tender-bar">
        {tenderName !== null && (
          <span className="tender-bar__name" title={tenderName}>
            {tenderName}
          </span>
        )}
        <nav className="workflow-nav" aria-label="Tender workflow">
          {STEPS.map((step) => (
            <NavLink
              key={step.label}
              to={step.path ? `${base}/${step.path}` : base}
              // Documents is the tender's index route; without `end` it would
              // also count as current on every other step.
              end={step.path === ""}
              className="workflow-nav__link"
            >
              {step.label}
            </NavLink>
          ))}
          <span className="workflow-nav__divider" aria-hidden="true" />
          <NavLink to={`${base}/graph`} className="workflow-nav__link">
            Graph
          </NavLink>
        </nav>
        {shownStatus !== null && (
          <span className="tender-bar__run">
            <span className="tender-bar__run-label">Run</span>
            <StatusBadge {...describeStatus("run", shownStatus)} />
            {atGate !== null && tenderId && (
              <span className="tender-bar__run-at">
                at <Link to={gateScreenPath(tenderId, atGate)}>{GATE_SCREENS[atGate].label}</Link>
              </span>
            )}
          </span>
        )}
      </div>
      <PageContent />
    </RunStatusContext.Provider>
  );
}
