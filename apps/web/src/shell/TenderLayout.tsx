import { NavLink, useParams } from "react-router-dom";
import { PageContent } from "./AppShell";
import "./TenderLayout.css";

// The workflow nav for every /tenders/:tenderId screen (DESIGN.md Layout,
// "Workflow nav", decision 7): a horizontal tab strip in pipeline order,
// Graph as a utility at the end, scrolling on its own at 390px. Plain
// links; NavLink sets aria-current="page" on the current step itself.
//
// Deliberately NOT here in batch 2 (DESIGN.md Layout, "Tender header"):
// - Tender name and client: no screen fetches the tender today
//   (getTender, api/client.ts:233, has no caller), and a restyle adds no
//   data fetching.
// - Run status badge: built in batch 3 with the requirements screen as its
//   first real caller, not as a slot with no caller now.
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

  return (
    <>
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
      <PageContent />
    </>
  );
}
