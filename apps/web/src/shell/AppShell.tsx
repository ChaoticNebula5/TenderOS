import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import "./AppShell.css";

// The app shell (DESIGN.md Layout, "App shell"): a 48px top bar over the
// content column. Rendered once, by App.tsx's layout route, for every
// signed-in screen; Login sits outside it.
//
// E2E constraints this file must keep (DESIGN.md Restyle batches, and the
// batch 2 locator audit of e2e/full-flow.spec.ts):
// - Everything here is OUTSIDE each screen's own <main> (:952 main > section).
// - No <strong> (:1451 is a strict-mode check on the page's only <strong>).
// - No <ul>/<li> (:1253 counts every <li> on the page, :1258 reads every
//   `ul li` as JSON) - links sit directly in <nav>.
// - No heading, <section>, <aside>, <table>, <button> or <input>: page-wide
//   locators look for each of those (:55, :389, :980, :344, :1083, :531).
// - No link whose name could contain a tender name (:513 on the dashboard).
// AppShell.test.tsx asserts the element rules.
//
// The account area shows the signed-in email only: /auth/me carries no
// organisation name (MeMembership is organization_id + role,
// api/client.ts:180-183), and Log out is deliberately not wired in this
// batch (no behaviour change in a restyle).

export function AppShell() {
  const { meResponse } = useAuth();

  return (
    <div className="app-shell">
      <a className="skip-link" href="#content">
        Skip to content
      </a>
      <header className="topbar">
        <Link to="/" className="wordmark topbar__wordmark">
          TenderOS
        </Link>
        <nav className="topbar__nav" aria-label="Primary">
          <NavLink to="/" end className="topbar__link">
            Bids
          </NavLink>
          <NavLink to="/talent" className="topbar__link">
            Talent
          </NavLink>
        </nav>
        {meResponse && <span className="topbar__account">{meResponse.user.email}</span>}
      </header>
      <Outlet />
    </div>
  );
}

// The content column: page gutter and width, and the skip link's target.
// A layout of its own (not part of AppShell) because tender screens put
// the workflow nav between the top bar and this column - TenderLayout
// renders the nav, then this. tabIndex -1 makes it a focusable skip target
// without adding a Tab stop (DESIGN.md: no tabIndex above 0).
export function PageContent() {
  return (
    <div id="content" className="page" tabIndex={-1}>
      <Outlet />
    </div>
  );
}
