import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { Button } from "../components/ui/Button";
import "./AppShell.css";

// The app shell (DESIGN.md Layout, "App shell"): a 48px top bar over the
// content column. Rendered once, by App.tsx's layout route, for every
// signed-in screen; Login sits outside it.
//
// E2E constraints this file must keep (DESIGN.md Restyle batches, and the
// batch 2 locator audit of e2e/full-flow.spec.ts):
// - Everything here is OUTSIDE each screen's own <main> (step 9 counts
//   `main > section`; steps 13 and 14 read `main strong`, `main li` and
//   `main ul li`).
// - No heading, <section>, <aside>, <table> or <input>: page-wide locators
//   look for each of those (gatePanel(), setup's résumé import, steps 3, 10
//   and 12).
// - One <button>, Log out, and no other (plan item 39). Every button locator
//   in the spec has a name, and none of those names is part of "Logging out..."
//   or "Log out e2e-<suffix>@example.com", the name it has on the spec's user.
// - No link whose name could contain a tender name (step 2, on the dashboard).
// AppShell.test.tsx asserts the element rules.
//
// The account area shows the signed-in email, not an organisation name:
// /auth/me carries none (MeMembership is organization_id + role,
// api/client.ts:180-183).

export function AppShell() {
  const { meResponse, logout } = useAuth();
  const [loggingOut, setLoggingOut] = useState(false);

  // Plan item 39. logout() can take up to 5 s (client.ts) and resolves whether
  // or not the server call worked. It ends the session, and RequireAuth then
  // lands on /login: this does not navigate itself, because the router's update
  // would race RequireAuth's redirect, and the redirect would win.
  async function handleLogOut() {
    setLoggingOut(true);
    await logout();
    setLoggingOut(false);
  }

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
        {meResponse && (
          <div className="topbar__account">
            <span className="topbar__email">{meResponse.user.email}</span>
            {/* The email is hidden below 600px, so the button's name says who is
                signed in. It starts with the visible label (WCAG 2.5.3). */}
            <Button
              variant="quiet"
              className="topbar__logout"
              aria-label={loggingOut ? undefined : `Log out ${meResponse.user.email}`}
              busy={loggingOut}
              busyLabel="Logging out..."
              onClick={() => void handleLogOut()}
            >
              Log out
            </Button>
          </div>
        )}
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
