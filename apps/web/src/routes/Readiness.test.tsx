import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
  FinalReadinessGateResponse,
  ReadinessReportResponse,
  WorkflowRunResponse,
} from "../api/client";
import { ApiError } from "../api/client";
import { TenderLayout } from "../shell/TenderLayout";
import { Readiness } from "./Readiness";

// Restyle batch 5. Pins the readiness contract: a stored report (GET on
// mount) never shows a <strong> or the word "Ready"; only a report
// generated in this visit shows the verdict <strong>, text exactly
// "Ready"/"Not ready" (full-flow.spec.ts:1451-1452); the "Blockers" <h2>'s
// following-sibling <ul> holds each message (:1454-1456, :1479). Fake data.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    getReadiness: vi.fn(),
    generateReadiness: vi.fn(),
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    decideGate: vi.fn(),
  };
});

const getReadiness = vi.mocked(apiClient.getReadiness);
const generateReadiness = vi.mocked(apiClient.generateReadiness);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const D6 = "Unresolved critical red-team finding: deselected_fact_in_use.";

function report(overrides: Partial<ReadinessReportResponse> = {}): ReadinessReportResponse {
  return {
    id: "rep-1",
    tender_id: "t1",
    tender_state_version: 8,
    counts: {
      requirements_total: 2,
      critical_major_redteam_findings: 0,
      some_new_count: 4,
    } as unknown as ReadinessReportResponse["counts"],
    blockers: [],
    ready: true,
    generated_at: "2026-10-11T16:29:26Z",
    ...overrides,
  };
}

function run(status: string): WorkflowRunResponse {
  return {
    id: "run-1",
    tender_id: "t1",
    langgraph_thread_id: "th",
    status,
    plan_version: 1,
    prompt_bundle_version: "v1",
    started_at: null,
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function gate(): FinalReadinessGateResponse {
  return {
    gate_id: "g1",
    workflow_run_id: "run-1",
    gate_type: "final_readiness",
    status: "pending",
    allowed_decisions: ["approve", "reject", "request_changes"],
    targets: [],
    decision_payload: null,
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: null,
    summary: { ready: true, blockers: [], counts: null, degraded_stages: [] },
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/readiness"]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route path="readiness" element={<Readiness />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Readiness (restyle batch 5)", () => {
  it("shows a stored 'Ready' report with no <strong> and never the word 'Ready'", async () => {
    getReadiness.mockResolvedValue(report({ ready: true }));
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    expect(await screen.findByText(/Last stored report: state version 8/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /no blockers at that version\. Generate a report to check the current state\./,
      ),
    ).toBeInTheDocument();
    expect(container.querySelectorAll("strong")).toHaveLength(0);
    expect(container.querySelector("main")!.textContent).not.toMatch(/\bReady\b/);
  });

  it("shows exactly one <strong> verdict only after Generate, with the exact text (full-flow.spec.ts:1451-1452)", async () => {
    const user = userEvent.setup();
    getReadiness.mockResolvedValue(report({ ready: true }));
    generateReadiness.mockResolvedValue(
      report({
        ready: false,
        tender_state_version: 13,
        blockers: [
          {
            category: "critical_redteam_finding",
            target_type: "redteam_finding",
            target_id: "f1",
            message: D6,
          },
        ],
      }),
    );
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findByText(/Last stored report/);
    await user.click(screen.getByRole("button", { name: "Generate readiness report" }));
    await waitFor(() => expect(container.querySelectorAll("strong")).toHaveLength(1));
    expect(container.querySelector("strong")!.textContent).toBe("Not ready");
    expect(screen.getByText(/Report for state version 13, generated/)).toBeInTheDocument();
    expect(screen.queryByText(/Last stored report/)).toBeNull();
  });

  it("a generated ready report reads exactly 'Ready'", async () => {
    const user = userEvent.setup();
    getReadiness.mockRejectedValue(new ApiError(404, "none"));
    generateReadiness.mockResolvedValue(report({ ready: true, tender_state_version: 13 }));
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    expect(await screen.findByText("No readiness report yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Generate readiness report" }));
    await waitFor(() => expect(container.querySelector("strong")?.textContent).toBe("Ready"));
  });

  it("keeps the blockers <ul> the 'Blockers' <h2>'s following sibling, each li holding the message (full-flow.spec.ts:1454-1456, :1479)", async () => {
    getReadiness.mockResolvedValue(
      report({
        ready: false,
        blockers: [
          {
            category: "critical_redteam_finding",
            target_type: "redteam_finding",
            target_id: "f1",
            message: D6,
          },
        ],
      }),
    );
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    const h2 = await screen.findByRole("heading", { name: "Blockers" });
    let sibling = h2.nextElementSibling;
    while (sibling && sibling.tagName !== "UL") sibling = sibling.nextElementSibling;
    expect(sibling).not.toBeNull();
    expect(sibling!.parentElement).toBe(h2.parentElement);
    const items = Array.from(sibling!.querySelectorAll(":scope > li")).map(
      (li) => li.textContent ?? "",
    );
    expect(items).toHaveLength(1);
    expect(items[0]).toContain(D6);
    expect(container.querySelectorAll("table")).toHaveLength(0);
  });

  // Polish pass (P7): identical blockers become one <li> with a count, the
  // message verbatim (:1464 looks for it inside an <li>; :1459 needs at
  // least one); categories read from a hand-written map.
  it("groups identical blockers into one li with a count and names categories in words", async () => {
    const d6 = (id: string) => ({
      category: "critical_redteam_finding",
      target_type: "redteam_finding",
      target_id: id,
      message: D6,
    });
    getReadiness.mockResolvedValue(
      report({
        ready: false,
        blockers: [
          d6("f1"),
          d6("f2"),
          d6("f3"),
          {
            category: "role_with_no_selected_candidate",
            target_type: "role_requirement",
            target_id: "r1",
            message: "Role 'Quality Lead' has no selected candidate.",
          },
          {
            category: "some_new_category",
            target_type: "x",
            target_id: "x1",
            message: "Something new.",
          },
        ],
      }),
    );
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    const h2 = await screen.findByRole("heading", { name: "Blockers" });
    let ul = h2.nextElementSibling;
    while (ul && ul.tagName !== "UL") ul = ul.nextElementSibling;
    const items = Array.from(ul!.querySelectorAll(":scope > li")).map((li) => li.textContent ?? "");
    expect(items).toEqual([
      `Critical red-team finding ${D6} (3 blockers)`,
      "Role without a selected candidate Role 'Quality Lead' has no selected candidate.",
      "Some new category Something new.",
    ]);
    expect(items.filter((t) => t.includes(D6))).toHaveLength(1);
    // The count never splits across lines; the space before it is outside it.
    const count = ul!.querySelector(".readiness__blocker-count")!;
    expect(count.textContent).toBe("(3 blockers)");
    // The stored line still counts every blocker, not every line.
    expect(container.textContent).toMatch(/5 blockers at that version/);
  });

  it("labels the counts by hand, falls back to humanize, and renders them as a <dl>", async () => {
    getReadiness.mockResolvedValue(report());
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findByText("Requirements");
    expect(screen.getByText("Open critical or major red-team findings")).toBeInTheDocument();
    expect(screen.getByText("Some new count")).toBeInTheDocument();
    expect(container.querySelectorAll(".readiness__counts dt")).toHaveLength(3);
  });

  it("reports the run status, keeps the gate below the report, heading and panel adjacent (full-flow.spec.ts:56)", async () => {
    getReadiness.mockResolvedValue(report());
    listWorkflowRuns.mockResolvedValue([run("waiting_for_human")]);
    listGates.mockResolvedValue([gate()]);
    const { container } = renderScreen();

    await screen.findByText("Awaiting decision");
    await waitFor(() =>
      expect(container.querySelector(".tender-bar .status-badge")).toHaveTextContent(
        "Waiting for review",
      ),
    );
    const gateHeading = screen.getByRole("heading", { name: "Final readiness gate" });
    expect(gateHeading.nextElementSibling?.tagName).toBe("SECTION");
    const blockers = screen.getByRole("heading", { name: "Blockers" });
    expect(
      blockers.compareDocumentPosition(gateHeading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The gate panel's own snapshot badge is a <span>; still no <strong>.
    expect(container.querySelectorAll("strong")).toHaveLength(0);
  });

  // Batch 5 fix 1: a GET that answers after Generate must not overwrite the
  // generated report with the stored one.
  it("keeps the generated verdict when the mount GET answers after Generate", async () => {
    const user = userEvent.setup();
    let resolveGet!: (r: ReadinessReportResponse) => void;
    getReadiness.mockReturnValue(new Promise((r) => (resolveGet = r)));
    generateReadiness.mockResolvedValue(report({ ready: false, tender_state_version: 13 }));
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await user.click(screen.getByRole("button", { name: "Generate readiness report" }));
    await waitFor(() => expect(container.querySelector("strong")?.textContent).toBe("Not ready"));
    resolveGet(report({ ready: true, tender_state_version: 8 }));
    // Let the late GET's continuation run.
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelectorAll("strong")).toHaveLength(1);
    expect(container.querySelector("strong")!.textContent).toBe("Not ready");
    expect(screen.queryByText(/Last stored report/)).toBeNull();
    expect(screen.getByText(/Report for state version 13/)).toBeInTheDocument();
  });
});
