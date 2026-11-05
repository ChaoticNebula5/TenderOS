import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MeResponse } from "../api/client";
import { AuthContext } from "../auth/authContextValue";
import { AppShell, PageContent } from "./AppShell";
import { TenderLayout } from "./TenderLayout";

// Guards the shell side of e2e/full-flow.spec.ts's page-wide locators
// (DESIGN.md Restyle batches and the batch 2 locator audit). If any of
// these elements appears in the shell, a spec line that scans the whole
// page can match it - several of them silently (:1253 / :1258 count and
// parse every <li>), not with a clear failure. A <button> is the one change:
// the shell has exactly one, Log out (plan item 39), and every button locator
// in the spec has a name that isn't part of it.

afterEach(() => {
  cleanup();
});

const MEMBER: MeResponse = {
  user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
  memberships: [{ organization_id: "org-1", role: "member" }],
};

function renderShellAt(path: string, logout: () => Promise<void> = async () => {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider
        value={{
          meResponse: MEMBER,
          organizationId: "org-1",
          refresh: async () => {},
          logout,
          loggedOut: false,
        }}
      >
        <Routes>
          <Route element={<AppShell />}>
            <Route element={<PageContent />}>
              <Route path="/" element={<main>dashboard screen</main>} />
            </Route>
            <Route path="/tenders/:tenderId" element={<TenderLayout />}>
              <Route index element={<main>documents screen</main>} />
              <Route path="requirements" element={<main>requirements screen</main>} />
            </Route>
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

// Everything the shell drew, i.e. the page minus the screen's own <main>.
function shellParts(container: HTMLElement): Element[] {
  return [...container.querySelectorAll(".app-shell > *, .workflow-nav")].filter(
    (el) => el.tagName !== "MAIN" && !el.classList.contains("page"),
  );
}

const FORBIDDEN = "strong, ul, li, aside, table, section, h1, h2, h3, h4, h5, h6, input";

describe("AppShell", () => {
  it.each(["/", "/tenders/t1", "/tenders/t1/requirements"])(
    "at %s contains none of the elements the E2E scans page-wide",
    (path) => {
      const { container } = renderShellAt(path);
      const found = shellParts(container).flatMap((el) => [...el.querySelectorAll(FORBIDDEN)]);
      expect(found.map((el) => el.tagName)).toEqual([]);
    },
  );

  it.each(["/", "/tenders/t1", "/tenders/t1/requirements"])(
    "at %s has one button, Log out, in the top bar beside the email",
    (path) => {
      const { container } = renderShellAt(path);
      const buttons = shellParts(container).flatMap((el) => [...el.querySelectorAll("button")]);
      expect(buttons.map((el) => el.textContent)).toEqual(["Log out"]);

      const button = screen.getByRole("button", { name: "Log out" });
      expect(button.closest("header")).not.toBeNull();
      expect(button.previousElementSibling).toHaveTextContent("person@example.com");
    },
  );

  it("disables the button only while logout() runs", async () => {
    let finish: () => void = () => {};
    const logout = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    const user = userEvent.setup();
    renderShellAt("/", logout);
    expect(screen.getByRole("button", { name: "Log out" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Log out" }));

    expect(logout).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Logging out..." })).toBeDisabled();

    finish();

    expect(await screen.findByRole("button", { name: "Log out" })).toBeEnabled();
  });

  it("keeps the shell outside <main>", () => {
    const { container } = renderShellAt("/tenders/t1/requirements");
    const main = container.querySelector("main");
    expect(main).not.toBeNull();
    expect(main!.closest("header, nav")).toBeNull();
    expect(main!.querySelector("header, nav")).toBeNull();
  });

  it("marks only Documents current on the tender index, only Requirements on requirements", () => {
    renderShellAt("/tenders/t1");
    expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute("aria-current", "page");
    cleanup();

    renderShellAt("/tenders/t1/requirements");
    expect(screen.getByRole("link", { name: "Requirements" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Documents" })).not.toHaveAttribute("aria-current");
  });

  it("shows the workflow nav on tender screens only", () => {
    renderShellAt("/");
    expect(screen.queryByRole("navigation", { name: "Tender workflow" })).toBeNull();
    expect(screen.getByRole("link", { name: "Bids" })).toHaveAttribute("aria-current", "page");
  });

  it("points the skip link at the content column", () => {
    const { container } = renderShellAt("/");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute(
      "href",
      "#content",
    );
    expect(container.querySelector("#content")).toHaveAttribute("tabindex", "-1");
  });
});
