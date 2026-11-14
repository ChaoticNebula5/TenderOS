import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { GateResponse } from "../api/client";
import { refreshPendingGate } from "./refreshGate";

// Plan item 73: a screen re-reads its pending gate after a row change, so
// the live counts follow; a late answer must never undo a decision.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return { ...actual, getGate: vi.fn() };
});

const getGate = vi.mocked(apiClient.getGate);

afterEach(() => {
  vi.clearAllMocks();
});

function gate(status: string, pending: number): GateResponse {
  return {
    gate_id: "g1",
    gate_type: "compliance_review",
    workflow_run_id: "r1",
    status,
    targets: [],
    summary: { compliance_entry_count: 2, pending_count: 2, high_risk_count: 0 },
    live_summary: { compliance_entry_count: 2, pending_count: pending, high_risk_count: 0 },
    allowed_decisions: ["approve", "reject", "request_changes"],
    decision_payload: null,
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: null,
  } as GateResponse;
}

function useGate(initial: GateResponse | null) {
  return useState<GateResponse | null>(initial);
}

describe("refreshPendingGate", () => {
  it("replaces a pending gate with the fresh read", async () => {
    getGate.mockResolvedValue(gate("pending", 0));
    const { result } = renderHook(() => useGate(gate("pending", 2)));

    await act(() => refreshPendingGate(result.current[0], result.current[1]));

    expect(getGate).toHaveBeenCalledWith("g1");
    expect(result.current[0]?.live_summary).toEqual({
      compliance_entry_count: 2,
      pending_count: 0,
      high_risk_count: 0,
    });
  });

  it("never puts back a pending gate over one decided meanwhile", async () => {
    let answer: (value: GateResponse) => void = () => {};
    getGate.mockReturnValue(
      new Promise<GateResponse>((resolve) => {
        answer = resolve;
      }),
    );
    const { result } = renderHook(() => useGate(gate("pending", 2)));

    const refreshing = refreshPendingGate(result.current[0], result.current[1]);
    act(() => result.current[1](gate("approved", 2)));
    await act(async () => {
      answer(gate("pending", 0));
      await refreshing;
    });

    expect(result.current[0]?.status).toBe("approved");
  });

  it("reads nothing for a decided gate or no gate", async () => {
    const { result } = renderHook(() => useGate(gate("approved", 2)));

    await act(() => refreshPendingGate(result.current[0], result.current[1]));
    await act(() => refreshPendingGate(null, result.current[1]));

    expect(getGate).not.toHaveBeenCalled();
  });

  it("keeps the counts already shown when the read fails", async () => {
    getGate.mockRejectedValue(new apiClient.ApiError(503, {}));
    const { result } = renderHook(() => useGate(gate("pending", 2)));

    await act(() => refreshPendingGate(result.current[0], result.current[1]));

    expect(result.current[0]?.live_summary).toEqual({
      compliance_entry_count: 2,
      pending_count: 2,
      high_risk_count: 0,
    });
  });
});
