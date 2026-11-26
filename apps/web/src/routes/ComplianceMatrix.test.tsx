import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
  ComplianceResponse,
  ComplianceReviewGateResponse,
  WorkflowRunResponse,
} from "../api/client";
import { TenderLayout } from "../shell/TenderLayout";
import { describeStatus } from "../status/statusMap";
import { ComplianceMatrix } from "./ComplianceMatrix";

// Restyle batch 4. Renders the real screen inside the real TenderLayout
// with the API boundary stubbed; every fixture value is fake. Several tests
// pin a full-flow.spec.ts contract (line cited in each test name) so a
// restyle that breaks the E2E fails here first.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listCompliance: vi.fn(),
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    approveCompliance: vi.fn(),
    getCompliance: vi.fn(),
    patchCompliance: vi.fn(),
    decideGate: vi.fn(),
  };
});

const listCompliance = vi.mocked(apiClient.listCompliance);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const approveCompliance = vi.mocked(apiClient.approveCompliance);
const getCompliance = vi.mocked(apiClient.getCompliance);
const patchCompliance = vi.mocked(apiClient.patchCompliance);
const decideGate = vi.mocked(apiClient.decideGate);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const REQ_ID = "aaaaaaaa-0000-0000-0000-000000000001";

function run(status: string): WorkflowRunResponse {
  return {
    id: "run-1",
    tender_id: "t1",
    langgraph_thread_id: "thread-1",
    status,
    plan_version: 1,
    prompt_bundle_version: "v1",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function entry(overrides: Partial<ComplianceResponse>): ComplianceResponse {
  // Plan item 121: by default the analysis's own entry, its automated
  // answer the status shown.
  const status = overrides.status ?? "non_compliant";
  return {
    id: "c1",
    requirement_id: REQ_ID,
    status,
    reasoning_summary: null,
    automated_status: status,
    automated_reasoning_summary: null,
    decided_by: "analysis",
    confidence: 0.8999999761581421,
    human_approved: false,
    last_evaluated_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function gate(status: "pending" | "approved"): ComplianceReviewGateResponse {
  return {
    gate_id: "g1",
    workflow_run_id: "run-1",
    gate_type: "compliance_review",
    status,
    allowed_decisions: ["approve", "reject", "request_changes"],
    targets: [],
    decision_payload: null,
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: status === "pending" ? null : "2026-01-02T00:00:00Z",
    summary: { compliance_entry_count: 1, pending_count: 1, high_risk_count: 0 },
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/compliance"]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route path="compliance" element={<ComplianceMatrix />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function firstRow(container: HTMLElement) {
  return container.querySelector("table")!.querySelector("tbody tr") as HTMLElement;
}

describe("ComplianceMatrix (restyle batch 4)", () => {
  it("keeps option values as backend words with mapped labels, and overrides by value (full-flow.spec.ts:714, :736)", async () => {
    const user = userEvent.setup();
    listCompliance.mockResolvedValue([entry({ status: "non_compliant" })]);
    listWorkflowRuns.mockResolvedValue([]);
    patchCompliance.mockResolvedValue(entry({ status: "fully_compliant", human_approved: true }));
    const { container } = renderScreen();

    const select = await screen.findByRole("combobox", { name: "Change status" });
    expect((select as HTMLSelectElement).value).toBe("non_compliant");
    const options = Array.from((select as HTMLSelectElement).options);
    expect(options.map((o) => o.value)).toEqual([
      "fully_compliant",
      "partially_compliant",
      "non_compliant",
      "clarification_required",
      "evidence_unavailable",
      "not_reviewed",
    ]);
    expect(options.map((o) => o.textContent)).toEqual([
      "Compliant",
      "Partly compliant",
      "Not compliant",
      "Clarification needed",
      "No evidence",
      "Not reviewed",
    ]);
    expect(firstRow(container).querySelectorAll("select")).toHaveLength(1);

    await user.selectOptions(select, "fully_compliant");
    expect(patchCompliance).toHaveBeenCalledWith("c1", {
      status: "fully_compliant",
      reasoning_summary: "Overridden by reviewer.",
    });
    // :737 - the override approves server-side, and the row's Approve
    // button is gone from the DOM, not merely disabled.
    await waitFor(() =>
      expect(within(firstRow(container)).queryByRole("button", { name: "Approve" })).toBeNull(),
    );
  });

  it("removes the row's Approve button once approved, rather than disabling it (full-flow.spec.ts:728)", async () => {
    const user = userEvent.setup();
    listCompliance.mockResolvedValue([entry({ status: "fully_compliant" })]);
    listWorkflowRuns.mockResolvedValue([]);
    approveCompliance.mockResolvedValue(entry({ status: "fully_compliant", human_approved: true }));
    const { container } = renderScreen();

    await screen.findByText("Not approved");
    await user.click(within(firstRow(container)).getByRole("button", { name: "Approve" }));
    // Scoped to the row: the column header is also the word "Approved".
    await waitFor(() =>
      expect(within(firstRow(container)).getByText("Approved")).toBeInTheDocument(),
    );
    expect(within(firstRow(container)).queryByRole("button")).toBeNull();
    // Plan item 59 (Q4): the approval names the status the row showed.
    expect(approveCompliance).toHaveBeenCalledWith("c1", "fully_compliant");
  });

  it("says whose decision a row is, and the analysis's answer only where it differs (plan item 121)", async () => {
    listCompliance.mockResolvedValue([
      entry({ id: "c1", status: "fully_compliant" }),
      entry({
        id: "c2",
        status: "fully_compliant",
        automated_status: "evidence_unavailable",
        decided_by: "reviewer",
        human_approved: true,
      }),
      entry({ id: "c3", status: "non_compliant", decided_by: "reviewer", human_approved: true }),
    ]);
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findAllByRole("combobox", { name: "Change status" });
    const rows = Array.from(container.querySelector("table")!.querySelectorAll("tbody tr"));
    expect(rows[0].querySelector(".compliance__note")).toBeNull();
    expect(rows[1].querySelector(".compliance__note")?.textContent).toBe(
      "Reviewer's decision. The analysis says: No evidence",
    );
    expect(rows[2].querySelector(".compliance__note")?.textContent).toBe("Reviewer's decision");
  });

  it("marks a reopened decision Reopened, keeps one select and one Approve button, and re-approves the reviewer's status (plan item 121; full-flow.spec.ts:736-745)", async () => {
    const user = userEvent.setup();
    const reopened = entry({
      status: "fully_compliant",
      automated_status: "partially_compliant",
      decided_by: "reviewer",
      human_approved: false,
    });
    listCompliance.mockResolvedValue([reopened, entry({ id: "c2", status: "non_compliant" })]);
    listWorkflowRuns.mockResolvedValue([]);
    approveCompliance.mockResolvedValue({ ...reopened, human_approved: true });
    const { container } = renderScreen();

    await screen.findByText("Reopened");
    const rows = Array.from(container.querySelector("table")!.querySelectorAll("tbody tr"));
    // An analysis entry that is not approved is not reopened.
    expect(within(rows[1] as HTMLElement).getByText("Not approved")).toBeInTheDocument();
    const row = rows[0] as HTMLElement;
    expect(row.querySelectorAll("select")).toHaveLength(1);
    expect(within(row).getAllByRole("button", { name: /Approve/ })).toHaveLength(1);
    await user.click(within(row).getByRole("button", { name: "Approve" }));
    expect(approveCompliance).toHaveBeenCalledWith("c1", "fully_compliant");
    await waitFor(() => expect(within(row).getByText("Approved")).toBeInTheDocument());
  });

  it("reads the entry again and says why when it changed after the page loaded (409 COMPLIANCE_ENTRY_CHANGED)", async () => {
    const user = userEvent.setup();
    listCompliance.mockResolvedValue([entry({ status: "non_compliant" })]);
    listWorkflowRuns.mockResolvedValue([]);
    approveCompliance.mockRejectedValue(
      new apiClient.ApiError(409, {
        error: {
          code: "COMPLIANCE_ENTRY_CHANGED",
          message: "This entry's status changed since the page loaded.",
          details: { current_status: "partially_compliant" },
        },
      }),
    );
    getCompliance.mockResolvedValue(entry({ status: "partially_compliant" }));
    const { container } = renderScreen();

    await screen.findByText("Not approved");
    await user.click(within(firstRow(container)).getByRole("button", { name: "Approve" }));

    const label = describeStatus("compliance", "partially_compliant").label;
    expect(
      await screen.findByText(
        `This entry changed to ${label} after the page loaded, so it was not approved. ` +
          "Review it, then approve again.",
      ),
    ).toBeInTheDocument();
    // The status the row showed, not the one it changed to.
    expect(approveCompliance).toHaveBeenCalledWith("c1", "non_compliant");
    expect(getCompliance).toHaveBeenCalledWith("c1");
    // The row shows the current status, still unapproved, with Approve offered again.
    expect(
      within(firstRow(container)).getByRole("combobox", { name: "Change status" }),
    ).toHaveValue("partially_compliant");
    expect(within(firstRow(container)).getByText("Not approved")).toBeInTheDocument();
    expect(
      within(firstRow(container)).getByRole("button", { name: "Approve" }),
    ).toBeInTheDocument();
  });

  it("keeps the plain failure message for any other approve error", async () => {
    const user = userEvent.setup();
    listCompliance.mockResolvedValue([entry({ status: "fully_compliant" })]);
    listWorkflowRuns.mockResolvedValue([]);
    approveCompliance.mockRejectedValue(
      new apiClient.ApiError(500, { error: { code: "INTERNAL_ERROR" } }),
    );
    const { container } = renderScreen();

    await screen.findByText("Not approved");
    await user.click(within(firstRow(container)).getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Approve failed (500)")).toBeInTheDocument();
    expect(getCompliance).not.toHaveBeenCalled();
  });

  it("is the page's first table, its first cell is the raw requirement id, and tbody holds only data rows (full-flow.spec.ts:696, :701, :712)", async () => {
    listCompliance.mockResolvedValue([entry({}), entry({ id: "c2", requirement_id: "r-2" })]);
    listWorkflowRuns.mockResolvedValue([run("waiting_for_human")]);
    listGates.mockResolvedValue([gate("pending")]);
    const { container } = renderScreen();

    await screen.findByText("Awaiting decision");
    const tables = container.querySelectorAll("table");
    expect(tables).toHaveLength(1);
    expect(tables[0].querySelectorAll("tbody tr")).toHaveLength(2);
    expect(firstRow(container).querySelector("td")!.textContent).toBe(REQ_ID);
  });

  it("shows confidence as a whole percent and a missing one as 'Not scored'", async () => {
    listCompliance.mockResolvedValue([
      entry({}),
      entry({ id: "c2", confidence: null, human_approved: true }),
    ]);
    listWorkflowRuns.mockResolvedValue([]);
    renderScreen();

    expect(await screen.findByText("90%")).toBeInTheDocument();
    expect(screen.getByText("Not scored")).toBeInTheDocument();
    expect(screen.getByText("2 entries, 1 approved")).toBeInTheDocument();
  });

  it("puts a pending gate above the table and a decided one below, heading and panel still siblings (full-flow.spec.ts:56), and reports the run status", async () => {
    const user = userEvent.setup();
    listCompliance.mockResolvedValue([entry({})]);
    listWorkflowRuns.mockResolvedValue([run("waiting_for_human")]);
    listGates.mockResolvedValue([gate("pending")]);
    decideGate.mockResolvedValue(gate("approved"));
    const { container } = renderScreen();

    await screen.findByText("Awaiting decision");
    await waitFor(() =>
      expect(container.querySelector(".tender-bar .status-badge")).toHaveTextContent(
        "Waiting for review",
      ),
    );
    const heading = screen.getByRole("heading", { name: "Compliance review gate" });
    expect(heading.nextElementSibling?.tagName).toBe("SECTION");
    expect(
      heading.compareDocumentPosition(container.querySelector("table")!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    const panel = heading.nextElementSibling as HTMLElement;
    // What the server says once the decision has moved the run on.
    listWorkflowRuns.mockResolvedValue([run("running")]);
    await user.click(within(panel).getByRole("button", { name: "Approve" }));
    // Since plan item 74 the bar re-reads and shows the run's new status
    // instead of going blank.
    await waitFor(() =>
      expect(container.querySelector(".tender-bar .status-badge")).toHaveTextContent("Running"),
    );
    const decided = screen.getByRole("heading", { name: "Compliance review gate" });
    expect(decided.nextElementSibling?.tagName).toBe("SECTION");
    expect(
      decided.compareDocumentPosition(container.querySelector("table")!) &
        Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
  });

  it("shows the empty state only after the response, outside the table", async () => {
    let resolve!: (value: ComplianceResponse[]) => void;
    listCompliance.mockReturnValue(new Promise((r) => (resolve = r)));
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    expect(container.querySelector("table")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("No compliance entries yet")).toBeNull();
    resolve([]);
    const empty = await screen.findByText("No compliance entries yet");
    expect(empty.closest("table")).toBeNull();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(container.querySelector("table")).not.toHaveAttribute("aria-busy");
  });
});
