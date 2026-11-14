import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import { ApiError } from "../api/client";
import type { GateResponse, GateType, MeResponse, WorkflowRunResponse } from "../api/client";
import { AppRoutes } from "../App";
import { AuthContext } from "../auth/authContextValue";
import { GATE_SCREENS, gateScreenPath } from "./gateScreens";

// Plan item 73: no gate can pause a run without a screen. For every entry
// in the registry, the REAL route table (AppRoutes) is rendered at the
// entry's path with the tender's latest run waiting at a pending gate of
// that type, and the screen must show the entry's heading followed by the
// gate panel's <section> with its Approve button: the shape
// full-flow.spec.ts's gatePanel() finds. A registry entry pointing at a
// screen that shows no panel for its gate type fails here; a gate type
// with no entry fails tsc (the registry is a Record over the union); and a
// gate type the backend opens with no entry fails
// tests/unit/test_every_gate_has_a_screen.py.
//
// The tender bar (plan item 74) is checked on the way: it names the gate
// the run waits at and links to the same screen.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    getTender: vi.fn(),
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    getWorkflowRun: vi.fn(),
    listWorkflowTasks: vi.fn(),
    mintWorkflowEventsTicket: vi.fn(),
    listDocuments: vi.fn(),
    listRequirements: vi.fn(),
    listCompliance: vi.fn(),
    listRoles: vi.fn(),
    listPricingDocuments: vi.fn(),
    listPricingFindings: vi.fn(),
    listRedteamFindings: vi.fn(),
    getReadiness: vi.fn(),
    getActiveOperation: vi.fn(),
  };
});

const mocked = vi.mocked(apiClient);

// jsdom has no EventSource; the Documents page opens two through
// useEventStream. This stand-in only has to exist and stay quiet.
class QuietEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readyState = QuietEventSource.OPEN;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  addEventListener() {}
  removeEventListener() {}
  close() {
    this.readyState = QuietEventSource.CLOSED;
  }
}

const MEMBER: MeResponse = {
  user: { id: "u1", email: "person@example.com", display_name: null, is_active: true },
  memberships: [{ organization_id: "org-1", role: "admin" }],
};

const WAITING_RUN: WorkflowRunResponse = {
  id: "run-1",
  tender_id: "t1",
  langgraph_thread_id: "thread-1",
  status: "waiting_for_human",
  plan_version: 1,
  prompt_bundle_version: "v1",
  started_at: "2026-01-01T00:00:00Z",
  completed_at: null,
  created_at: "2026-01-01T00:00:00Z",
};

// A pending gate of each type, with a summary of the right shape (fake
// values).
const SUMMARIES: Record<GateType, Record<string, unknown>> = {
  requirement_review: {
    requirement_count: 2,
    mandatory_count: 1,
    unverified_mandatory_count: 1,
    submission_item_count: 0,
    clarification_count: 0,
  },
  cost_budget: { accumulated_cost_usd: 0, budget_usd: 0, overage_usd: 0 },
  compliance_review: { compliance_entry_count: 1, pending_count: 1, high_risk_count: 0 },
  resource_selection: {
    role_count: 1,
    candidate_count: 2,
    unselected_count: 2,
    unselected_role_count: 1,
  },
  commercial_approval: { finding_count: 1, pending_count: 1, critical_count: 0 },
  finding_resolution: { open_finding_count: 1, critical_count: 1 },
  final_readiness: { ready: false, blockers: [], counts: null, degraded_stages: [] },
};

function pendingGate(gateType: GateType): GateResponse {
  return {
    gate_id: `gate-${gateType}`,
    gate_type: gateType,
    workflow_run_id: "run-1",
    status: "pending",
    targets: [],
    summary: SUMMARIES[gateType],
    live_summary: gateType === "final_readiness" ? undefined : SUMMARIES[gateType],
    allowed_decisions: ["approve", "reject", "request_changes"],
    decision_payload: null,
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: null,
  } as GateResponse;
}

beforeEach(() => {
  vi.stubGlobal("EventSource", QuietEventSource);
  mocked.getTender.mockResolvedValue({ id: "t1", name: "Registry test bid" } as never);
  mocked.listWorkflowRuns.mockResolvedValue([WAITING_RUN]);
  mocked.getWorkflowRun.mockResolvedValue(WAITING_RUN);
  mocked.listWorkflowTasks.mockResolvedValue([]);
  mocked.mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
  mocked.listDocuments.mockResolvedValue([]);
  mocked.listRequirements.mockResolvedValue([]);
  mocked.listCompliance.mockResolvedValue([]);
  mocked.listRoles.mockResolvedValue([]);
  mocked.listPricingDocuments.mockResolvedValue([]);
  mocked.listPricingFindings.mockResolvedValue([]);
  mocked.listRedteamFindings.mockResolvedValue([]);
  mocked.getReadiness.mockRejectedValue(new ApiError(404, { detail: "No readiness report" }));
  mocked.getActiveOperation.mockResolvedValue(null);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function renderAt(path: string) {
  return render(
    <AuthContext.Provider
      value={{
        meResponse: MEMBER,
        organizationId: "org-1",
        refresh: async () => {},
        logout: async () => {},
        loggedOut: false,
      }}
    >
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("every gate type has a screen that shows its panel", () => {
  it.each(Object.entries(GATE_SCREENS) as [GateType, (typeof GATE_SCREENS)[GateType]][])(
    "%s",
    async (gateType, entry) => {
      mocked.listGates.mockResolvedValue([pendingGate(gateType)]);
      const path = gateScreenPath("t1", gateType);
      const { container } = renderAt(path);

      // Some screens render the heading with a "No open ... gate yet" line
      // until the gate has loaded, so wait for the panel itself.
      await waitFor(() => {
        const heading = screen.getByRole("heading", { name: entry.heading });
        expect(heading.nextElementSibling?.tagName).toBe("SECTION");
      });
      const heading = screen.getByRole("heading", { name: entry.heading });
      const panel = heading.nextElementSibling as HTMLElement;
      expect(within(panel).getByRole("button", { name: "Approve" })).toBeInTheDocument();

      // Plan item 74: the tender bar names this gate and links to its screen.
      await waitFor(() =>
        expect(container.querySelector(".tender-bar__run-at a")).toHaveAttribute("href", path),
      );
      expect(container.querySelector(".tender-bar__run-at")).toHaveTextContent(`at ${entry.label}`);
      expect(container.querySelector(".tender-bar__name")).toHaveTextContent("Registry test bid");
    },
  );
});
