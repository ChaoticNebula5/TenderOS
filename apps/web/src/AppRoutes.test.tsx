import { useState } from "react";
import type { ReactNode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MeResponse } from "./api/client";
import { AppRoutes } from "./App";
import { AuthContext } from "./auth/authContextValue";

// Restyle batch 2 moved RequireAuth from one wrapper per route to a single
// layout route around the app shell (App.tsx). destinationPreservation.test
// .tsx builds its own per-route tree, so it does not exercise that change;
// this file renders the REAL route tree (AppRoutes) and pins the same
// contract through it: an unauthenticated deep link goes to /login and,
// once signed in, comes back to that link - with the shell and workflow
// nav around it.
//
// The compliance screen's two load calls are stubbed (it is the deep-link
// target); nothing else on that route reaches the network.
vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    listCompliance: vi.fn().mockResolvedValue([]),
    listWorkflowRuns: vi.fn().mockResolvedValue([]),
  };
});

afterEach(() => {
  cleanup();
});

const MEMBER: MeResponse = {
  user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
  memberships: [{ organization_id: "org-1", role: "member" }],
};

// Same stand-in for AuthProvider as destinationPreservation.test.tsx: the
// button flips meResponse from null to a member, which is what a successful
// sign-in looks like from RequireAuth's and Login's point of view.
function AuthHarness({ children }: { children: ReactNode }) {
  const [meResponse, setMeResponse] = useState<MeResponse | null>(null);
  return (
    <>
      <button onClick={() => setMeResponse(MEMBER)}>simulate sign-in</button>
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

describe("AppRoutes: the shell's layout route keeps deep links", () => {
  it("redirects an unauthenticated deep link to login, then returns to it after sign-in", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tenders/t1/compliance"]}>
        <AuthHarness>
          <AppRoutes />
        </AuthHarness>
      </MemoryRouter>,
    );

    // Login is showing and the shell is not (Login sits outside it).
    expect(await screen.findByRole("heading", { level: 1, name: "TenderOS" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Tender workflow" })).toBeNull();

    await user.click(screen.getByText("simulate sign-in"));

    // Back on the compliance screen, inside the shell, with its step current.
    expect(
      await screen.findByRole("heading", { level: 1, name: "Compliance matrix" }),
    ).toBeInTheDocument();
    const workflow = screen.getByRole("navigation", { name: "Tender workflow" });
    expect(workflow.querySelector('[aria-current="page"]')).toHaveTextContent("Compliance");
    expect(screen.getByRole("link", { name: "Compliance" })).toHaveAttribute(
      "href",
      "/tenders/t1/compliance",
    );
  });
});
