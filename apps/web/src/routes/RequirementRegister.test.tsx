import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
  RequirementResponse,
  RequirementReviewGateResponse,
  WorkflowRunResponse,
} from "../api/client";
import { TenderLayout } from "../shell/TenderLayout";
import { RequirementRegister } from "./RequirementRegister";

// Restyle batch 3. RequirementRegister is the tender bar run badge's first
// real caller: it keeps the status of the run it already lists (for the
// gate) and reports it. These tests render the real screen inside the real
// TenderLayout with the API boundary stubbed. All fixture values are fake.
//
// The bar is updated from an effect (useReportRunStatus), so it lands one
// commit after the screen renders the status - assertions on the bar wait
// for it (waitFor) rather than reading it in the same tick.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listRequirements: vi.fn(),
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    decideGate: vi.fn(),
    getWorkflowRun: vi.fn(),
    patchRequirement: vi.fn(),
    verifyRequirement: vi.fn(),
  };
});

const listRequirements = vi.mocked(apiClient.listRequirements);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const decideGate = vi.mocked(apiClient.decideGate);
const getWorkflowRun = vi.mocked(apiClient.getWorkflowRun);
const patchRequirement = vi.mocked(apiClient.patchRequirement);
const verifyRequirement = vi.mocked(apiClient.verifyRequirement);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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

function requirement(overrides: Partial<RequirementResponse>): RequirementResponse {
  return {
    id: "r1",
    display_code: "STAFF-001",
    requirement_text: "The bidder shall name a delivery lead.",
    category: "staffing",
    mandatory: true,
    verified: true,
    human_reviewed: true,
    rejected_at: null,
    ...overrides,
  } as RequirementResponse;
}

function gate(status: RequirementReviewGateResponse["status"]): RequirementReviewGateResponse {
  return {
    gate_id: "g1",
    workflow_run_id: "run-1",
    gate_type: "requirement_review",
    status,
    allowed_decisions: ["approve", "reject", "request_changes"],
    targets: [],
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: status === "pending" ? null : "2026-01-02T00:00:00Z",
    summary: {
      requirement_count: 2,
      mandatory_count: 1,
      unverified_mandatory_count: 0,
      submission_item_count: 0,
      clarification_count: 0,
    },
  } as unknown as RequirementReviewGateResponse;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/requirements"]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route path="requirements" element={<RequirementRegister />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function barBadge(container: HTMLElement) {
  return container.querySelector(".tender-bar .status-badge");
}

describe("RequirementRegister (restyle batch 3)", () => {
  it("reports the latest run's status to the tender bar from the run list it already fetches", async () => {
    listRequirements.mockResolvedValue([requirement({})]);
    listWorkflowRuns.mockResolvedValue([run("waiting_for_human")]);
    listGates.mockResolvedValue([gate("pending")]);
    const { container } = renderScreen();

    await screen.findByText("Awaiting decision");
    await waitFor(() => expect(barBadge(container)).toHaveTextContent("Waiting for review"));
    expect(barBadge(container)?.closest("main")).toBeNull();
    // The screen's own reads, once each. Since plan item 74 the tender bar
    // reads the run list and gates itself too (it names the waiting gate),
    // so those are not counted here; no single-run fetch either way.
    expect(listRequirements).toHaveBeenCalledTimes(1);
    expect(getWorkflowRun).not.toHaveBeenCalled();
    // Plan item 74: the bar names the gate the run waits at, with a link.
    await waitFor(() =>
      expect(container.querySelector(".tender-bar__run-at a")).toHaveAttribute(
        "href",
        "/tenders/t1/requirements",
      ),
    );
  });

  it("shows no run badge when the tender has no run", async () => {
    listRequirements.mockResolvedValue([requirement({})]);
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findByText("STAFF-001");
    expect(await screen.findByText(/No open requirement review gate/)).toBeInTheDocument();
    expect(barBadge(container)).toBeNull();
  });

  it("clears the run badge after a gate decision, and moves the decided gate below the table", async () => {
    const user = userEvent.setup();
    listRequirements.mockResolvedValue([requirement({})]);
    listWorkflowRuns.mockResolvedValue([run("waiting_for_human")]);
    listGates.mockResolvedValue([gate("pending")]);
    decideGate.mockResolvedValue(gate("approved"));
    const { container } = renderScreen();

    // Query the heading only once the gate has loaded: before that, the
    // heading sits below the table with the "No open gate" text, and the
    // pending gate re-renders the pair above the table as new elements.
    await screen.findByText("Awaiting decision");
    await waitFor(() => expect(barBadge(container)).not.toBeNull());
    const heading = screen.getByRole("heading", { name: "Requirement review gate" });
    const table = container.querySelector("table")!;
    // Pending: heading and panel above the table, still siblings (full-flow.spec.ts:56).
    expect(heading.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(heading.nextElementSibling?.tagName).toBe("SECTION");

    // What the server says once the decision has moved the run on.
    listWorkflowRuns.mockResolvedValue([run("running")]);
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Approved")).toBeInTheDocument();
    // The screen withdraws its now-stale status; since plan item 74 the
    // bar re-reads and shows the run's new one instead of going blank.
    await waitFor(() => expect(barBadge(container)).toHaveTextContent("Running"));
    expect(container.querySelector(".tender-bar__run-at")).toBeNull();
    const decidedHeading = screen.getByRole("heading", { name: "Requirement review gate" });
    expect(decidedHeading.nextElementSibling?.tagName).toBe("SECTION");
    expect(
      decidedHeading.compareDocumentPosition(container.querySelector("table")!) &
        Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
  });

  it("keeps the spec's cell contract: Reviewed is the sixth cell and reads exactly 'yes' (full-flow.spec.ts:653)", async () => {
    listRequirements.mockResolvedValue([requirement({ human_reviewed: true })]);
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findByText("STAFF-001");
    const cells = container.querySelectorAll("tbody tr:first-child td");
    expect(cells[5].textContent).toBe("yes");
    expect(cells[3].textContent).toBe("yes");
  });

  it("shows Verified as a badge: Warning when mandatory and unverified, Neutral when optional", async () => {
    listRequirements.mockResolvedValue([
      requirement({ id: "a", display_code: "A-1", verified: true }),
      requirement({ id: "b", display_code: "B-1", verified: false, mandatory: true }),
      requirement({ id: "c", display_code: "C-1", verified: false, mandatory: false }),
    ]);
    listWorkflowRuns.mockResolvedValue([]);
    renderScreen();

    const rowOf = async (code: string) => (await screen.findByText(code)).closest("tr")!;
    expect(within(await rowOf("A-1")).getByText("Verified")).toHaveClass("status-badge--ready");
    expect(within(await rowOf("B-1")).getByText("Not verified")).toHaveClass(
      "status-badge--warning",
    );
    expect(within(await rowOf("C-1")).getByText("Not verified")).toHaveClass(
      "status-badge--neutral",
    );
    expect(screen.getByText(/3 requirements, 1 unverified mandatory/)).toBeInTheDocument();
  });

  it("dims a rejected row with a class and gives it no actions; other rows get the three actions", async () => {
    listRequirements.mockResolvedValue([
      requirement({ id: "a", display_code: "A-1" }),
      requirement({ id: "b", display_code: "B-1", rejected_at: "2026-01-01T00:00:00Z" }),
    ]);
    listWorkflowRuns.mockResolvedValue([]);
    renderScreen();

    const open = (await screen.findByText("A-1")).closest("tr")!;
    const rejected = screen.getByText("B-1").closest("tr")!;
    expect(rejected).toHaveClass("requirements__row--rejected");
    expect(rejected.getAttribute("style")).toBeNull();
    expect(within(rejected).queryAllByRole("button")).toEqual([]);
    expect(within(open).getByRole("button", { name: "Confirm" })).toHaveClass("btn--quiet");
    expect(within(open).getByRole("button", { name: "Reject" })).toHaveClass("btn--danger");
    expect(within(open).getByRole("button", { name: "Re-verify" })).toHaveClass("btn--quiet");
  });
});

// Upload-list race, sibling screens (useListReads.ts): Re-verify re-reads
// the whole list, and a Confirm on another row can land while that read is
// in flight. The read predates the confirm and must not undo it.
describe("a confirm that lands while Re-verify's re-read is in flight", () => {
  it("keeps the confirmed row reviewed", async () => {
    const user = userEvent.setup();
    const a = requirement({ id: "a", display_code: "A-1", human_reviewed: false });
    const b = requirement({ id: "b", display_code: "B-1", human_reviewed: false });
    let answerReread: (rows: RequirementResponse[]) => void = () => {};
    listRequirements.mockResolvedValueOnce([a, b]).mockReturnValueOnce(
      new Promise<RequirementResponse[]>((r) => {
        answerReread = r;
      }),
    );
    listWorkflowRuns.mockResolvedValue([]);
    verifyRequirement.mockResolvedValue(a);
    patchRequirement.mockResolvedValue({ ...b, human_reviewed: true });
    renderScreen();

    const rowA = (await screen.findByText("A-1")).closest("tr")!;
    const rowB = screen.getByText("B-1").closest("tr")!;
    const reviewed = (row: Element) => row.querySelectorAll("td")[5]?.textContent;
    await user.click(within(rowA).getByRole("button", { name: "Re-verify" }));
    await waitFor(() => expect(listRequirements).toHaveBeenCalledTimes(2));
    await user.click(within(rowB).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(reviewed(screen.getByText("B-1").closest("tr")!)).toBe("yes"));

    answerReread([a, b]);
    await new Promise((r) => setTimeout(r, 0));

    expect(reviewed(screen.getByText("B-1").closest("tr")!)).toBe("yes");
  });
});
