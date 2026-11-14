import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CommercialApprovalGateResponse,
  ComplianceReviewGateResponse,
  CostBudgetGateResponse,
  FinalReadinessGateResponse,
  FindingResolutionGateResponse,
  RequirementReviewGateResponse,
  ResourceSelectionGateResponse,
} from "../api/client";
import * as apiClient from "../api/client";
import { formatDate } from "../format/date";
import { GateDecisionPanel } from "./GateDecisionPanel";

// Real ApiError class preserved, only decideGate mocked - GateDecisionPanel
// does `err instanceof ApiError`, which breaks silently (falls to the
// generic error branch) against a mocked class that isn't the real one.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return { ...actual, decideGate: vi.fn() };
});

const decideGate = vi.mocked(apiClient.decideGate);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  // Belt-and-braces alongside each fake-timer test's own vi.useRealTimers()
  // - a real-timers reset here means a test that throws before reaching its
  // own cleanup still can't leak fake timers into the next test.
  vi.useRealTimers();
});

// The summary is a definition grid (restyle batch 3): each count is a <dt>
// term followed by its <dd> value, where it used to be one "Label: n" <li>.
function expectSummaryItem(term: string, value: string) {
  const dt = screen.getByText(term, { selector: "dt" });
  expect(dt.nextElementSibling?.tagName).toBe("DD");
  expect(dt.nextElementSibling).toHaveTextContent(value);
}

const BASE = {
  gate_id: "11111111-1111-1111-1111-111111111111",
  workflow_run_id: "22222222-2222-2222-2222-222222222222",
  status: "pending" as const,
  targets: [],
  allowed_decisions: ["approve", "reject", "request_changes"],
  decision_payload: null,
  decided_by: null,
  requested_at: "2026-01-01T00:00:00Z",
  decided_at: null,
};

function requirementReviewGate(): RequirementReviewGateResponse {
  return {
    ...BASE,
    gate_type: "requirement_review",
    summary: {
      requirement_count: 12,
      mandatory_count: 5,
      unverified_mandatory_count: 2,
      submission_item_count: 3,
      clarification_count: 1,
    },
  };
}

function complianceReviewGate(): ComplianceReviewGateResponse {
  return {
    ...BASE,
    gate_type: "compliance_review",
    summary: { compliance_entry_count: 8, pending_count: 3, high_risk_count: 1 },
  };
}

function resourceSelectionGate(): ResourceSelectionGateResponse {
  return {
    ...BASE,
    gate_type: "resource_selection",
    summary: { role_count: 4, candidate_count: 9, unselected_count: 2 },
  };
}

function commercialApprovalGate(): CommercialApprovalGateResponse {
  return {
    ...BASE,
    gate_type: "commercial_approval",
    summary: { finding_count: 6, pending_count: 4, critical_count: 1 },
  };
}

function findingResolutionGate(): FindingResolutionGateResponse {
  return {
    ...BASE,
    gate_type: "finding_resolution",
    summary: { open_finding_count: 3, critical_count: 2 },
  };
}

function finalReadinessGate(overrides: { ready: boolean }): FinalReadinessGateResponse {
  return {
    ...BASE,
    gate_type: "final_readiness",
    summary: {
      ready: overrides.ready,
      blockers: overrides.ready
        ? []
        : [
            {
              category: "unresolved_mandatory_requirement",
              target_type: "requirement",
              target_id: "33333333-3333-3333-3333-333333333333",
              message: "REQ-001 has no resolved compliance decision.",
            },
          ],
      counts: null,
      degraded_stages: [],
    },
  };
}

describe("GateSummary - each gate_type renders its own typed fields", () => {
  it("requirement_review", () => {
    render(<GateDecisionPanel gate={requirementReviewGate()} onDecided={vi.fn()} />);
    expectSummaryItem("Requirements", "12");
    expectSummaryItem("Mandatory", "5");
    expectSummaryItem("Unverified mandatory", "2");
    expectSummaryItem("Submission items", "3");
    expectSummaryItem("Clarifications", "1");
  });

  it("compliance_review", () => {
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);
    expectSummaryItem("Compliance entries", "8");
    expectSummaryItem("Pending approval", "3");
    expectSummaryItem("High risk", "1");
  });

  it("resource_selection", () => {
    // Plan item 73 (item 79's C3): candidate_count counts (role, candidate)
    // matches, and the old unselected_count (matches) is not shown.
    render(<GateDecisionPanel gate={resourceSelectionGate()} onDecided={vi.fn()} />);
    expectSummaryItem("Roles", "4");
    expectSummaryItem("Candidate matches", "9");
    expect(screen.queryByText("Unselected roles")).not.toBeInTheDocument();
    expect(screen.queryByText("Roles without a pick")).not.toBeInTheDocument();
  });

  it("resource_selection with roles counted: 'Roles without a pick'", () => {
    const gate: ResourceSelectionGateResponse = {
      ...resourceSelectionGate(),
      summary: { role_count: 1, candidate_count: 4, unselected_count: 3, unselected_role_count: 0 },
    };
    render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);
    expectSummaryItem("Roles", "1");
    expectSummaryItem("Candidate matches", "4");
    expectSummaryItem("Roles without a pick", "0");
  });

  it("commercial_approval", () => {
    render(<GateDecisionPanel gate={commercialApprovalGate()} onDecided={vi.fn()} />);
    expectSummaryItem("Findings", "6");
    expectSummaryItem("Pending", "4");
    expectSummaryItem("Critical", "1");
  });

  it("finding_resolution", () => {
    render(<GateDecisionPanel gate={findingResolutionGate()} onDecided={vi.fn()} />);
    expectSummaryItem("Open findings", "3");
    expectSummaryItem("Critical", "2");
  });

  it("final_readiness - not ready, shows its blocker message", () => {
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />);
    expect(screen.getByText("Not ready")).toHaveClass("status-badge--blocked");
    expect(screen.getByText("REQ-001 has no resolved compliance decision.")).toBeInTheDocument();
  });
});

describe("final_readiness waive flag", () => {
  it("shows the waive checkbox when not ready, and omits it when ready", () => {
    const { rerender } = render(
      <GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />,
    );
    expect(screen.getByLabelText("Waive outstanding blockers")).toBeInTheDocument();

    rerender(<GateDecisionPanel gate={finalReadinessGate({ ready: true })} onDecided={vi.fn()} />);
    expect(screen.queryByLabelText("Waive outstanding blockers")).not.toBeInTheDocument();
  });

  it("checking the box and approving sends waive: true", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    decideGate.mockResolvedValue(finalReadinessGate({ ready: false }));
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={onDecided} />);

    await user.click(screen.getByLabelText("Waive outstanding blockers"));
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(decideGate).toHaveBeenCalledWith(
      BASE.gate_id,
      expect.objectContaining({ decision: "approve", waive: true }),
      expect.any(String),
    );
    expect(onDecided).toHaveBeenCalled();
  });

  it("approving without checking the box sends waive: false", async () => {
    const user = userEvent.setup();
    decideGate.mockResolvedValue(finalReadinessGate({ ready: false }));
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(decideGate).toHaveBeenCalledWith(
      BASE.gate_id,
      expect.objectContaining({ waive: false }),
      expect.any(String),
    );
  });

  it("a non-final_readiness gate always sends waive: false, even though it has no checkbox to check", async () => {
    const user = userEvent.setup();
    decideGate.mockResolvedValue(complianceReviewGate());
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(decideGate).toHaveBeenCalledWith(
      BASE.gate_id,
      expect.objectContaining({ waive: false }),
      expect.any(String),
    );
  });
});

describe("decision buttons", () => {
  it("reject sends decision: reject and notes", async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    decideGate.mockResolvedValue(complianceReviewGate());
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={onDecided} />);

    await user.type(screen.getByLabelText("Notes (optional)"), "not ready yet");
    await user.click(screen.getByRole("button", { name: "Reject" }));

    expect(decideGate).toHaveBeenCalledWith(
      BASE.gate_id,
      expect.objectContaining({ decision: "reject", notes: "not ready yet" }),
      expect.any(String),
    );
    expect(onDecided).toHaveBeenCalledWith(complianceReviewGate());
  });

  it("request_changes sends decision: request_changes", async () => {
    const user = userEvent.setup();
    decideGate.mockResolvedValue(complianceReviewGate());
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Request changes" }));

    expect(decideGate).toHaveBeenCalledWith(
      BASE.gate_id,
      expect.objectContaining({ decision: "request_changes" }),
      expect.any(String),
    );
  });

  it("blank notes are sent as null, not an empty string", async () => {
    const user = userEvent.setup();
    decideGate.mockResolvedValue(complianceReviewGate());
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(decideGate).toHaveBeenCalledWith(
      BASE.gate_id,
      expect.objectContaining({ notes: null }),
      expect.any(String),
    );
  });

  it("a 409 response shows the stale-readiness message immediately, with no retry", async () => {
    // retryTransient classifies 409 as terminal (an allowlist of retryable
    // statuses - 408/5xx - not a denylist), for two independent reasons:
    // Build Guide line 2707's IDEMPOTENCY_CONFLICT ("same key + different
    // request returns 409") is deterministic and permanent - retrying with
    // the same key this component already reuses can never resolve it - and
    // this gate's own stale-readiness 409 isn't plausibly self-resolving
    // within a few seconds of backoff either. No fake timers needed: a
    // terminal error re-throws on the first attempt.
    const user = userEvent.setup();
    decideGate.mockRejectedValue(
      new apiClient.ApiError(409, {
        error: {
          code: "READINESS_NOT_WAIVED",
          message:
            "Readiness has unresolved blockers. Resolve them, or resubmit with waive=true to approve anyway.",
        },
      }),
    );
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText(
        "This gate's readiness is stale or unresolved blockers block approval.",
      ),
    ).toBeInTheDocument();
    expect(decideGate).toHaveBeenCalledTimes(1);
  });

  it("a 409 with any other code shows that error's own message, not the readiness one", async () => {
    // Phase 4 blocker 2: the readiness message used to be shown for every
    // 409, including "your first attempt is still being processed". Only
    // READINESS_NOT_WAIVED means readiness; IDEMPOTENCY_IN_PROGRESS is also
    // not retried here (retryTransient's set is 408/5xx), so the user sees
    // the server's own wait message after one call.
    const user = userEvent.setup();
    decideGate.mockRejectedValue(
      new apiClient.ApiError(
        409,
        {
          error: {
            code: "IDEMPOTENCY_IN_PROGRESS",
            message: "This request is already being processed. Please wait a moment and try again.",
          },
        },
        42,
      ),
    );
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText(
        "This request is already being processed. Please wait a moment and try again.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This gate's readiness is stale or unresolved blockers block approval."),
    ).not.toBeInTheDocument();
    expect(decideGate).toHaveBeenCalledTimes(1);
  });

  it("a non-409 failure shows the generic error message, after retryTransient's own retries exhaust", async () => {
    vi.useFakeTimers();
    decideGate.mockRejectedValue(new apiClient.ApiError(500, {}));
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(screen.getByText("Failed to record the decision.")).toBeInTheDocument();
    expect(decideGate).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });

  it("a terminal 401 shows the generic error message immediately, with no retry", async () => {
    // The one case retryTransient must NOT retry - confirms the wiring
    // didn't accidentally make every failure transient. No fake timers
    // needed: a terminal error re-throws on the first attempt, so there's
    // no backoff delay for a test to wait out.
    const user = userEvent.setup();
    decideGate.mockRejectedValue(new apiClient.ApiError(401, {}));
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Failed to record the decision.")).toBeInTheDocument();
    expect(decideGate).toHaveBeenCalledTimes(1);
  });

  it("reuses the same idempotency key across retries of one decision", async () => {
    vi.useFakeTimers();
    decideGate.mockRejectedValue(new apiClient.ApiError(500, {}));
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(decideGate).toHaveBeenCalledTimes(3);
    const keysUsed = decideGate.mock.calls.map((call) => call[2]);
    expect(new Set(keysUsed).size).toBe(1);
    vi.useRealTimers();
  });
});

describe("an already-decided gate", () => {
  it("renders the decision instead of the action buttons", () => {
    const decided: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      status: "approved",
      decided_at: "2026-01-02T00:00:00Z",
    };
    render(<GateDecisionPanel gate={decided} onDecided={vi.fn()} />);

    expect(screen.getByText("approved", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
});

// Plan item 73, decision 3: live counts first, the snapshot below.
function gridValues(grid: Element): Record<string, string> {
  const values: Record<string, string> = {};
  grid.querySelectorAll("dt").forEach((dt) => {
    values[dt.textContent ?? ""] = dt.nextElementSibling?.textContent ?? "";
  });
  return values;
}

describe("plan item 73: live counts, then the snapshot taken when the gate opened", () => {
  it("shows the live counts first and the differing snapshot under its label", () => {
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      summary: { compliance_entry_count: 2, pending_count: 2, high_risk_count: 1 },
      live_summary: { compliance_entry_count: 2, pending_count: 0, high_risk_count: 0 },
    };
    const { container } = render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    const labels = [...container.querySelectorAll(".gate-panel__asof")].map((p) => p.textContent);
    expect(labels).toEqual(["Now", "When this gate opened · " + formatDate(BASE.requested_at)]);
    const [live, snapshot] = [...container.querySelectorAll("dl.gate-summary")];
    expect(gridValues(live)).toEqual({
      "Compliance entries": "2",
      "Pending approval": "0",
      "High risk": "0",
    });
    expect(gridValues(snapshot)).toEqual({
      "Compliance entries": "2",
      "Pending approval": "2",
      "High risk": "1",
    });
  });

  it("says the counts are unchanged instead of showing the same grid twice", () => {
    const counts = { compliance_entry_count: 2, pending_count: 1, high_risk_count: 0 };
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      summary: counts,
      live_summary: { ...counts },
    };
    const { container } = render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(container.querySelectorAll("dl.gate-summary")).toHaveLength(1);
    expect(
      screen.getByText("Unchanged since this gate opened · " + formatDate(BASE.requested_at)),
    ).toBeInTheDocument();
  });

  it("shows a decided gate's snapshot alone, even if live counts were sent", () => {
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      status: "approved",
      decided_at: "2026-01-02T00:00:00Z",
      live_summary: { compliance_entry_count: 9, pending_count: 9, high_risk_count: 9 },
    };
    const { container } = render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(screen.queryByText("Now")).not.toBeInTheDocument();
    const grids = container.querySelectorAll("dl.gate-summary");
    expect(grids).toHaveLength(1);
    expect(gridValues(grids[0])["Pending approval"]).not.toBe("9");
  });

  it("shows the cost budget gate's spend in US dollars, and its description while pending", () => {
    const gate: CostBudgetGateResponse = {
      ...BASE,
      gate_type: "cost_budget",
      summary: { accumulated_cost_usd: 25.5, budget_usd: 25, overage_usd: 0.5 },
      live_summary: { accumulated_cost_usd: 25.5, budget_usd: 25, overage_usd: 0.5 },
    };
    const { container } = render(
      <GateDecisionPanel gate={gate} onDecided={vi.fn()} description="Why it paused." />,
    );

    expect(gridValues(container.querySelector("dl.gate-summary")!)).toEqual({
      Spent: "$25.50",
      Budget: "$25.00",
      "Over budget by": "$0.50",
    });
    expect(screen.getByText("Why it paused.")).toHaveClass("gate-panel__description");
  });

  it("drops the description once the gate is decided", () => {
    const gate: FindingResolutionGateResponse = {
      ...findingResolutionGate(),
      status: "approved",
      decided_at: "2026-01-02T00:00:00Z",
    };
    render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} description="Why it paused." />);

    expect(screen.queryByText("Why it paused.")).not.toBeInTheDocument();
  });
});

describe("plan items 71 and 72: what a decided gate recorded", () => {
  it("shows the reviewer's notes on a gate decided with Request changes", () => {
    const gate: RequirementReviewGateResponse = {
      ...requirementReviewGate(),
      status: "changes_requested",
      decision_payload: {
        decision: "request_changes",
        notes: "Upload the missing annex.",
        waived: false,
      },
      decided_at: "2026-01-02T00:00:00Z",
    };
    const { container } = render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(screen.getByText("Changes requested")).toHaveClass("status-badge--blocked");
    expect(container.querySelector(".gate-panel__notes")).toHaveTextContent(
      "Reviewer's notes: Upload the missing annex.",
    );
    expect(container.querySelector(".gate-panel__waived")).toBeNull();
    expect(container.querySelector("strong")).toBeNull();
  });

  it("shows no notes line when the decision had none", () => {
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      status: "approved",
      decision_payload: { decision: "approve", notes: null, waived: false },
      decided_at: "2026-01-02T00:00:00Z",
    };
    const { container } = render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(container.querySelector(".gate-panel__notes")).toBeNull();
  });

  it("says when a final gate's approval waived outstanding blockers", () => {
    const gate: FinalReadinessGateResponse = {
      ...finalReadinessGate({ ready: false }),
      status: "approved",
      decision_payload: { decision: "approve", notes: null, waived: true, waived_blockers: [] },
      decided_at: "2026-01-02T00:00:00Z",
    };
    render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(screen.getByText("Approved with outstanding blockers waived.")).toBeInTheDocument();
  });

  it("labels a gate a cancel closed 'Closed by cancel', not 'Rejected'", () => {
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      status: "rejected",
      decision_payload: { decision: null, closed_by: "run_cancelled" },
      decided_at: "2026-01-02T00:00:00Z",
    };
    render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(screen.getByText("Closed by cancel")).toHaveClass("status-badge--neutral");
    expect(screen.queryByText("Rejected")).not.toBeInTheDocument();
    expect(screen.getByText(`Closed ${formatDate("2026-01-02T00:00:00Z")}`)).toBeInTheDocument();
  });
});

describe("restyle batch 3: status, decided line, busy state", () => {
  it.each([
    ["pending", "Awaiting decision", "blocked"],
    ["approved", "Approved", "ready"],
    ["rejected", "Rejected", "failed"],
    ["changes_requested", "Changes requested", "blocked"],
  ] as const)("a %s gate shows its status badge", (status, label, tone) => {
    const decided = status !== "pending";
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      status,
      decided_at: decided ? "2026-01-02T00:00:00Z" : null,
    };
    render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(screen.getByText(label)).toHaveClass("status-badge", `status-badge--${tone}`);
    expect(screen.queryByRole("button", { name: "Approve" }) !== null).toBe(!decided);
  });

  it("shows a decided gate's date through formatDate, with no raw ISO string and no <strong>", () => {
    const iso = "2026-01-02T00:00:00Z";
    const gate: ComplianceReviewGateResponse = {
      ...complianceReviewGate(),
      status: "approved",
      decided_at: iso,
    };
    const { container } = render(<GateDecisionPanel gate={gate} onDecided={vi.fn()} />);

    expect(screen.getByText(`Decided ${formatDate(iso)}`)).toBeInTheDocument();
    expect(container.textContent).not.toContain(iso);
    expect(container.querySelector("strong")).toBeNull();
  });

  it("labels the summary as the snapshot taken when the gate opened", () => {
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);
    expect(
      screen.getByText(`When this gate opened · ${formatDate(BASE.requested_at)}`),
    ).toBeInTheDocument();
  });

  it("while a decision is in flight: the clicked button is aria-busy and all three are disabled", async () => {
    const user = userEvent.setup();
    let resolve: (gate: ComplianceReviewGateResponse) => void = () => {};
    decideGate.mockReturnValue(
      new Promise<ComplianceReviewGateResponse>((r) => {
        resolve = r;
      }),
    );
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Request changes" }));

    expect(screen.getByRole("button", { name: "Request changes" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByRole("button", { name: "Approve" })).not.toHaveAttribute("aria-busy");
    for (const name of ["Approve", "Request changes", "Reject"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }

    await act(async () => resolve(complianceReviewGate()));
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("gives Approve, Request changes and Reject the primary, secondary and danger variants", () => {
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Approve" })).toHaveClass("btn--primary");
    expect(screen.getByRole("button", { name: "Request changes" })).toHaveClass("btn--secondary");
    expect(screen.getByRole("button", { name: "Reject" })).toHaveClass("btn--danger");
  });
});
