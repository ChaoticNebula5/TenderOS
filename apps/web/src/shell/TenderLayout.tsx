import { useState } from "react";
import { NavLink, useParams } from "react-router-dom";
import { StatusBadge } from "../components/ui/StatusBadge";
import { describeStatus } from "../status/statusMap";
import { PageContent } from "./AppShell";
import { RunStatusContext } from "./runStatus";
import "./TenderLayout.css";

// The workflow nav for every /tenders/:tenderId screen (DESIGN.md Layout,
// "Workflow nav", decision 7): a horizontal tab strip in pipeline order,
// Graph as a utility at the end, scrolling on its own at 390px. Plain
// links; NavLink sets aria-current="page" on the current step itself.
//
// Run status badge (batch 3): the tender bar shows the latest run's status
// when - and only when - the current screen reports one through
// useReportRunStatus (runStatus.ts). Screens report only a status they
// already fetched; TenderLayout itself fetches nothing. No report, or a
// report of null, shows no badge at all: an unknown status is left blank,
// not guessed. It is a <span> (StatusBadge), never <strong>: the readiness
// page's E2E asserts on its only <strong> (full-flow.spec.ts:1451).
//
// Still NOT here (DESIGN.md Layout, "Deferred from batch 2"): tender name
// and client - no screen fetches the tender today (getTender,
// api/client.ts:233, has no caller), and a restyle adds no data fetching.
//
// Links sit directly in <nav>, no <ul>/<li>: full-flow.spec.ts:1253 counts
// every <li> on the page and :1258 parses every `ul li` as JSON.

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

export function TenderLayout() {
  const { tenderId } = useParams();
  const base = `/tenders/${tenderId}`;
  const [runStatus, setRunStatus] = useState<string | null>(null);

  return (
    <RunStatusContext.Provider value={setRunStatus}>
      <div className="tender-bar">
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
        {runStatus !== null && (
          <span className="tender-bar__run">
            <span className="tender-bar__run-label">Run</span>
            <StatusBadge {...describeStatus("run", runStatus)} />
          </span>
        )}
      </div>
      <PageContent />
    </RunStatusContext.Provider>
  );
}
