import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
  ResourceSelectionGateResponse,
  RoleMatchResponse,
  RoleRequirementResponse,
  WorkflowRunResponse,
} from "../api/client";
import { TenderLayout } from "../shell/TenderLayout";
import { TeamWorkspace } from "./TeamWorkspace";

// Restyle batch 4. Real screen inside the real TenderLayout, API boundary
// stubbed, every fixture value fake. Tests named with a full-flow.spec.ts
// line pin that line's DOM contract.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listRoles: vi.fn(),
    listMatches: vi.fn(),
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    selectCandidate: vi.fn(),
  };
});

const listRoles = vi.mocked(apiClient.listRoles);
const listMatches = vi.mocked(apiClient.listMatches);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const selectCandidate = vi.mocked(apiClient.selectCandidate);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const EMPLOYEE_ID = "eeeeeeee-0000-0000-0000-000000000001";

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

function role(overrides: Partial<RoleRequirementResponse>): RoleRequirementResponse {
  return {
    id: "role-1",
    tender_id: "t1",
    role_name: "Project Manager",
    min_experience_years: 5,
    required_skills: [],
    required_certs: [],
    ...overrides,
  };
}

function match(overrides: Partial<RoleMatchResponse>): RoleMatchResponse {
  return {
    id: "m1",
    role_requirement_id: "role-1",
    employee_knowledge_item_id: EMPLOYEE_ID,
    score: 0.82,
    strengths: [],
    gaps: [],
    human_selected: false,
    ...overrides,
  };
}

function gate(): ResourceSelectionGateResponse {
  return {
    gate_id: "g1",
    workflow_run_id: "run-1",
    gate_type: "resource_selection",
    status: "pending",
    allowed_decisions: ["approve", "reject", "request_changes"],
    targets: [],
    decision_payload: null,
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: null,
    summary: { role_count: 1, candidate_count: 1, unselected_count: 1 },
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/team"]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route path="team" element={<TeamWorkspace />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

// The spec's own locator: a <section> that has the "Candidates" heading.
function candidateSection(container: HTMLElement): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Candidates" });
  const section = heading.closest("section");
  expect(section).not.toBeNull();
  expect(container.contains(section)).toBe(true);
  return section as HTMLElement;
}

describe("TeamWorkspace (restyle batch 4)", () => {
  it("roles are the page's first table with the role name first (full-flow.spec.ts:780, :795)", async () => {
    listRoles.mockResolvedValue([role({}), role({ id: "role-2", min_experience_years: null })]);
    listWorkflowRuns.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findAllByRole("button", { name: "View candidates" });
    const rows = container.querySelector("table")!.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector("td")!.textContent).toBe("Project Manager");
    expect(screen.getByText("5 years")).toBeInTheDocument();
    expect(screen.getByText("None stated")).toBeInTheDocument();
    // Polish pass (P1): a "Roles" heading names the table, outside any
    // <section>, so the spec's section-with-heading-"Candidates" match (:827)
    // still finds only the candidates section.
    const roles = screen.getByRole("heading", { name: "Roles" });
    expect(roles.closest("section")).toBeNull();
    expect(
      roles.compareDocumentPosition(container.querySelector("table")!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryAllByRole("heading", { name: /candidates/i })).toHaveLength(0);
  });

  it("candidates: a section with the Candidates heading, one row per match, first cell exactly the raw employee id (full-flow.spec.ts:827, :852, :855)", async () => {
    const user = userEvent.setup();
    listRoles.mockResolvedValue([role({})]);
    listWorkflowRuns.mockResolvedValue([]);
    listMatches.mockResolvedValue([
      match({}),
      match({ id: "m2", employee_knowledge_item_id: "other", score: null }),
    ]);
    const { container } = renderScreen();

    await user.click(await screen.findByRole("button", { name: "View candidates" }));
    const section = candidateSection(container);
    await waitFor(() => expect(section.querySelectorAll("tbody tr")).toHaveLength(2));
    const firstCell = section.querySelector("tbody tr td")!;
    expect(firstCell.textContent).toBe(EMPLOYEE_ID);
    expect(firstCell.children).toHaveLength(0);
    expect(within(section).getByText("82%")).toBeInTheDocument();
    expect(within(section).getByText("Not scored")).toBeInTheDocument();
    expect(within(section).getByText("For Project Manager")).toBeInTheDocument();
  });

  it("flips the row's button from exactly 'Select' to 'Deselect' and badges the row Selected (full-flow.spec.ts:877-887)", async () => {
    const user = userEvent.setup();
    listRoles.mockResolvedValue([role({})]);
    listWorkflowRuns.mockResolvedValue([]);
    listMatches.mockResolvedValue([match({})]);
    selectCandidate.mockResolvedValue(match({ human_selected: true }));
    const { container } = renderScreen();

    await user.click(await screen.findByRole("button", { name: "View candidates" }));
    const row = await waitFor(() => {
      const r = candidateSection(container).querySelector("tbody tr");
      expect(r).not.toBeNull();
      return r as HTMLElement;
    });
    expect(within(row).getByText("Not selected")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Select", exact: true }));
    expect(selectCandidate).toHaveBeenCalledWith("role-1", EMPLOYEE_ID, true, expect.any(String));
    await waitFor(() =>
      expect(within(row).queryByRole("button", { name: "Select", exact: true })).toBeNull(),
    );
    expect(within(row).getByRole("button", { name: "Deselect" })).toBeInTheDocument();
    expect(within(row).getByText("Selected")).toBeInTheDocument();
  });

  it("does not say 'no candidates' while the role's candidates are still loading", async () => {
    const user = userEvent.setup();
    listRoles.mockResolvedValue([role({})]);
    listWorkflowRuns.mockResolvedValue([]);
    let resolve!: (value: RoleMatchResponse[]) => void;
    listMatches.mockReturnValue(new Promise((r) => (resolve = r)));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "View candidates" }));
    expect(screen.getByText("Loading candidates...")).toBeInTheDocument();
    expect(screen.queryByText("No candidates for this role yet")).toBeNull();
    resolve([]);
    expect(await screen.findByText("No candidates for this role yet")).toBeInTheDocument();
    expect(screen.queryByText("Loading candidates...")).toBeNull();
  });

  it("puts the pending gate first, its panel the heading's next sibling and not the Candidates section (full-flow.spec.ts:56), and reports the run", async () => {
    const user = userEvent.setup();
    listRoles.mockResolvedValue([role({})]);
    listWorkflowRuns.mockResolvedValue([run("waiting_for_human")]);
    listGates.mockResolvedValue([gate()]);
    listMatches.mockResolvedValue([match({})]);
    const { container } = renderScreen();

    await screen.findByText("Awaiting decision");
    await user.click(screen.getByRole("button", { name: "View candidates" }));
    await waitFor(() =>
      expect(candidateSection(container).querySelector("tbody tr")).not.toBeNull(),
    );
    const heading = screen.getByRole("heading", { name: "Resource selection gate" });
    const panel = heading.nextElementSibling as HTMLElement;
    expect(panel.tagName).toBe("SECTION");
    expect(panel.classList.contains("gate-panel")).toBe(true);
    await waitFor(() =>
      expect(container.querySelector(".tender-bar .status-badge")).toHaveTextContent(
        "Waiting for review",
      ),
    );
  });
});
