import { useState } from "react";
import type { ReactNode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import type { MeResponse } from "../api/client";
import { Login } from "../routes/Login";
import { AuthContext } from "./authContextValue";
import { RequireAuth } from "./RequireAuth";

// B17 Phase 1 - see KNOWN_LIMITATIONS.md, "[B16, owner B17] Login.tsx's
// 'already logged in' guard drops the user's original destination on any
// transient /auth/me failure." Deterministic by design: this exercises the
// real RequireAuth.tsx + Login.tsx contract (both are the actual production
// components, not mocks) against a hand-controlled AuthContext value, so it
// answers "does the destination survive" without depending on retryTransient's
// own timing, a mocked me() HTTP chain, or a live server - the live E2E run
// found this bug once already at real API rate-limit cost; this test is what
// lets the same question be answered in milliseconds from now on.

afterEach(() => {
  cleanup();
});

const MEMBER: MeResponse = {
  user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
  memberships: [{ organization_id: "org-1", role: "member" }],
};

// Stands in for the real AuthProvider (AuthContext.tsx) - deliberately not
// used directly here, so this test's own pass/fail is about RequireAuth/
// Login's contract with AuthContext, not entangled with AuthContext's own
// refresh()/retryTransient timing, which useEventStream.test.ts's sibling
// pattern and GateDecisionPanel.test.tsx already exercise separately. The
// button is the test's only hook into the harness - clicking it is what a
// real AuthContext.refresh() success looks like from RequireAuth/Login's own
// point of view: meResponse flips from null to a real value, while Login is
// already mounted and showing the form (the exact "transient failure, then
// recovery, mid-flow" shape the real bug needs).
function AuthHarness({ children }: { children: ReactNode }) {
  const [meResponse, setMeResponse] = useState<MeResponse | null>(null);
  return (
    <>
      <button onClick={() => setMeResponse(MEMBER)}>simulate auth/me recovering</button>
      <AuthContext.Provider
        value={{
          meResponse,
          organizationId: meResponse?.memberships[0]?.organization_id ?? null,
          refresh: async () => setMeResponse(MEMBER),
          logout: () => setMeResponse(null),
        }}
      >
        {children}
      </AuthContext.Provider>
    </>
  );
}

function renderAt(initialPath: string) {
  render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthHarness>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <div>Bid dashboard content</div>
              </RequireAuth>
            }
          />
          <Route
            path="/tenders/:tenderId/compliance"
            element={
              <RequireAuth>
                <div>Compliance matrix content</div>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthHarness>
    </MemoryRouter>,
  );
}

describe("destination survives a transient auth failure mid-flow", () => {
  it("a deep link redirected to /login by a not-yet-authenticated RequireAuth lands back on that same link, not /, once meResponse recovers", async () => {
    const user = userEvent.setup();
    renderAt("/tenders/t1/compliance");

    // RequireAuth sees meResponse === null (this harness's initial state,
    // standing in for a transient /auth/me failure that hasn't recovered
    // yet) and redirects - confirmed by the login form actually being on
    // screen, not by reading router internals.
    expect(await screen.findByText("TenderOS")).toBeInTheDocument();
    expect(screen.queryByText("Compliance matrix content")).not.toBeInTheDocument();

    // The recovery: meResponse flips to a real value while Login.tsx is
    // still the mounted screen - exactly what a delayed but eventually
    // successful refresh() looks like from here.
    await user.click(screen.getByText("simulate auth/me recovering"));

    // Login.tsx's own top-of-render guard now fires - asserting on the
    // ORIGINAL deep link's content, not a hardcoded "/", is what actually
    // pins the bug fixed: before this fix, this would render the bare "/"
    // route (BidDashboard in the real app) instead.
    expect(await screen.findByText("Compliance matrix content")).toBeInTheDocument();
  });

  it("landing on /login directly, with no prior redirect, still defaults to the app root", async () => {
    // Guards the OTHER branch of `from`'s fallback - state.from is only set
    // by RequireAuth's own redirect; arriving at /login directly (a
    // bookmark, a typed URL) has no `from` in history state at all, and
    // `(location.state as ...)?.from ?? "/"` needs to fall through to "/"
    // rather than throwing on the missing state or landing nowhere.
    const user = userEvent.setup();
    renderAt("/login");
    expect(await screen.findByText("TenderOS")).toBeInTheDocument();

    await user.click(screen.getByText("simulate auth/me recovering"));

    expect(await screen.findByText("Bid dashboard content")).toBeInTheDocument();
  });
});
