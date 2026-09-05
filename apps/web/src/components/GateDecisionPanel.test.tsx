import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CommercialApprovalGateResponse,
  ComplianceReviewGateResponse,
  FinalReadinessGateResponse,
  FindingResolutionGateResponse,
  RequirementReviewGateResponse,
  ResourceSelectionGateResponse,
} from "../api/client";
import * as apiClient from "../api/client";
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
    expect(screen.getByText("Requirements: 12")).toBeInTheDocument();
    expect(screen.getByText("Mandatory: 5")).toBeInTheDocument();
    expect(screen.getByText("Unverified mandatory: 2")).toBeInTheDocument();
    expect(screen.getByText("Submission items: 3")).toBeInTheDocument();
    expect(screen.getByText("Clarifications: 1")).toBeInTheDocument();
  });

  it("compliance_review", () => {
    render(<GateDecisionPanel gate={complianceReviewGate()} onDecided={vi.fn()} />);
    expect(screen.getByText("Compliance entries: 8")).toBeInTheDocument();
    expect(screen.getByText("Pending approval: 3")).toBeInTheDocument();
    expect(screen.getByText("High risk: 1")).toBeInTheDocument();
  });

  it("resource_selection", () => {
    render(<GateDecisionPanel gate={resourceSelectionGate()} onDecided={vi.fn()} />);
    expect(screen.getByText("Roles: 4")).toBeInTheDocument();
    expect(screen.getByText("Candidates: 9")).toBeInTheDocument();
    expect(screen.getByText("Unselected roles: 2")).toBeInTheDocument();
  });

  it("commercial_approval", () => {
    render(<GateDecisionPanel gate={commercialApprovalGate()} onDecided={vi.fn()} />);
    expect(screen.getByText("Findings: 6")).toBeInTheDocument();
    expect(screen.getByText("Pending: 4")).toBeInTheDocument();
    expect(screen.getByText("Critical: 1")).toBeInTheDocument();
  });

  it("finding_resolution", () => {
    render(<GateDecisionPanel gate={findingResolutionGate()} onDecided={vi.fn()} />);
    expect(screen.getByText("Open findings: 3")).toBeInTheDocument();
    expect(screen.getByText("Critical: 2")).toBeInTheDocument();
  });

  it("final_readiness - not ready, shows its blocker message", () => {
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />);
    expect(screen.getByText("Ready: no")).toBeInTheDocument();
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

    await user.type(screen.getByPlaceholderText("Notes (optional)"), "not ready yet");
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
    // statuses - 408/429/5xx - not a denylist), for two independent reasons:
    // Build Guide line 2707's IDEMPOTENCY_CONFLICT ("same key + different
    // request returns 409") is deterministic and permanent - retrying with
    // the same key this component already reuses can never resolve it - and
    // this gate's own stale-readiness 409 isn't plausibly self-resolving
    // within a few seconds of backoff either. No fake timers needed: a
    // terminal error re-throws on the first attempt.
    const user = userEvent.setup();
    decideGate.mockRejectedValue(new apiClient.ApiError(409, { error: { code: "CONFLICT" } }));
    render(<GateDecisionPanel gate={finalReadinessGate({ ready: false })} onDecided={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText(
        "This gate's readiness is stale or unresolved blockers block approval.",
      ),
    ).toBeInTheDocument();
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
