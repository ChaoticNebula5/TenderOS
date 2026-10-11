import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { TenderLayout } from "./TenderLayout";
import { useReportRunStatus } from "./runStatus";

// The tender bar's run status badge (runStatus.ts). Stand-in screens report
// a status the way RequirementRegister and UploadAndProcessingStatus do;
// TenderLayout must show it outside <main>, show nothing when nothing (or
// null) is reported, and drop it when the reporting screen goes away.

afterEach(() => {
  cleanup();
});

function Reporting({ status }: { status: string | null }) {
  useReportRunStatus(status);
  return (
    <main>
      reporting screen <Link to="/tenders/t1/proposal">to a silent step</Link>
    </main>
  );
}

function renderAt(path: string, status: string | null) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route path="requirements" element={<Reporting status={status} />} />
          <Route path="proposal" element={<main>silent screen</main>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function tenderBar(container: HTMLElement): Element {
  const bar = container.querySelector(".tender-bar");
  expect(bar).not.toBeNull();
  return bar!;
}

describe("tender bar run status badge", () => {
  it("shows nothing when no screen has reported a status", () => {
    const { container } = renderAt("/tenders/t1/proposal", null);
    expect(tenderBar(container).querySelector(".status-badge")).toBeNull();
    expect(screen.queryByText("Run")).toBeNull();
  });

  it("shows nothing when the screen reports null (status unknown)", () => {
    const { container } = renderAt("/tenders/t1/requirements", null);
    expect(tenderBar(container).querySelector(".status-badge")).toBeNull();
  });

  it("shows the reported run status, labelled through the map, outside <main>", () => {
    const { container } = renderAt("/tenders/t1/requirements", "waiting_for_human");
    const badge = tenderBar(container).querySelector(".status-badge");
    expect(badge).toHaveTextContent("Waiting for review");
    expect(badge).toHaveClass("status-badge--blocked");
    expect(badge!.tagName).toBe("SPAN");
    expect(badge!.closest("main")).toBeNull();
    expect(container.querySelector("main .status-badge")).toBeNull();
  });

  it("clears the badge when navigating to a step that does not report", async () => {
    const user = userEvent.setup();
    const { container } = renderAt("/tenders/t1/requirements", "running");
    expect(tenderBar(container).querySelector(".status-badge")).toHaveTextContent("Running");

    await user.click(screen.getByRole("link", { name: "to a silent step" }));

    expect(await screen.findByText("silent screen")).toBeInTheDocument();
    expect(tenderBar(container).querySelector(".status-badge")).toBeNull();
  });

  it("puts none of the elements the E2E scans page-wide into the bar", () => {
    const { container } = renderAt("/tenders/t1/requirements", "failed");
    const forbidden = tenderBar(container).querySelectorAll(
      "strong, ul, li, aside, table, section, h1, h2, h3, h4, h5, h6, button, input",
    );
    expect([...forbidden].map((el) => el.tagName)).toEqual([]);
  });
});
