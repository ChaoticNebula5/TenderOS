import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { GateResponse, WorkflowRunResponse } from "../api/client";
import { useLatestRunGate } from "./useLatestRunGate";

// jsdom has no EventSource; the same minimal fake as useEventStream.test.ts.
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }
}

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    getWorkflowRun: vi.fn(),
    mintWorkflowEventsTicket: vi.fn(),
  };
});

const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const getWorkflowRun = vi.mocked(apiClient.getWorkflowRun);
const mintWorkflowEventsTicket = vi.mocked(apiClient.mintWorkflowEventsTicket);

function run(id: string, status: string): WorkflowRunResponse {
  return { id, status } as WorkflowRunResponse;
}

function gate(id: string, gateType: string, status: string): GateResponse {
  return { gate_id: id, gate_type: gateType, status } as GateResponse;
}

// The run's stream, once it has opened.
async function openStream(): Promise<FakeEventSource> {
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const source = FakeEventSource.instances[0]!;
  act(() => source.onopen?.());
  return source;
}

function push(source: FakeEventSource) {
  act(() => source.onmessage?.(new MessageEvent("message", { data: "{}" })));
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

describe("useLatestRunGate", () => {
  it("shows a gate that opens after the screen loaded, when the run's stream says so", async () => {
    listWorkflowRuns.mockResolvedValue([run("run-1", "running")]);
    getWorkflowRun.mockResolvedValue(run("run-1", "running"));
    listGates.mockResolvedValue([gate("g-req", "requirement_review", "approved")]);

    const { result } = renderHook(() =>
      useLatestRunGate("tender-1", "resource_selection", () => {}),
    );
    const source = await openStream();
    expect(result.current.gate).toBeNull();

    getWorkflowRun.mockResolvedValue(run("run-1", "waiting_for_human"));
    listGates.mockResolvedValue([
      gate("g-req", "requirement_review", "approved"),
      gate("g-res", "resource_selection", "pending"),
    ]);
    push(source);

    await waitFor(() => expect(result.current.gate?.gate_id).toBe("g-res"));
    expect(result.current.runStatus).toBe("waiting_for_human");
  });

  it("drops a read that started before this screen's decision and answered after it", async () => {
    listWorkflowRuns.mockResolvedValue([run("run-1", "waiting_for_human")]);
    getWorkflowRun.mockResolvedValue(run("run-1", "waiting_for_human"));
    listGates.mockResolvedValue([gate("g-com", "commercial_approval", "pending")]);

    const { result } = renderHook(() =>
      useLatestRunGate("tender-1", "commercial_approval", () => {}),
    );
    const source = await openStream();
    await waitFor(() => expect(result.current.gate?.status).toBe("pending"));

    // A message's read goes out, and is slow.
    let answerSlowRead: (gates: GateResponse[]) => void = () => {};
    listGates.mockImplementationOnce(
      () => new Promise<GateResponse[]>((resolve) => (answerSlowRead = resolve)),
    );
    push(source);
    await waitFor(() => expect(listGates).toHaveBeenCalledTimes(4));

    act(() => result.current.onDecided(gate("g-com", "commercial_approval", "approved")));
    expect(result.current.gate?.status).toBe("approved");
    expect(result.current.runStatus).toBeNull();

    // The slow read answers with the gate still pending, as it was when it
    // was read: dropped, so Approve is not offered again.
    await act(async () => answerSlowRead([gate("g-com", "commercial_approval", "pending")]));
    expect(result.current.gate?.status).toBe("approved");
    expect(result.current.runStatus).toBeNull();

    // The next read, made after the decision, is applied.
    getWorkflowRun.mockResolvedValue(run("run-1", "running"));
    listGates.mockResolvedValue([gate("g-com", "commercial_approval", "approved")]);
    push(source);
    await waitFor(() => expect(result.current.runStatus).toBe("running"));
    expect(result.current.gate?.status).toBe("approved");
  });

  it("closes the stream once the run ends", async () => {
    listWorkflowRuns.mockResolvedValue([run("run-1", "waiting_for_human")]);
    getWorkflowRun.mockResolvedValue(run("run-1", "waiting_for_human"));
    listGates.mockResolvedValue([gate("g-fin", "final_readiness", "pending")]);

    const { result } = renderHook(() => useLatestRunGate("tender-1", "final_readiness", () => {}));
    const source = await openStream();

    getWorkflowRun.mockResolvedValue(run("run-1", "completed"));
    listGates.mockResolvedValue([gate("g-fin", "final_readiness", "approved")]);
    push(source);

    await waitFor(() => expect(result.current.runStatus).toBe("completed"));
    expect(result.current.gate?.status).toBe("approved");
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("opens no stream for a run that has already ended", async () => {
    listWorkflowRuns.mockResolvedValue([run("run-1", "completed")]);
    listGates.mockResolvedValue([gate("g-req", "requirement_review", "approved")]);

    const { result } = renderHook(() =>
      useLatestRunGate("tender-1", "requirement_review", () => {}),
    );

    await waitFor(() => expect(result.current.gate?.gate_id).toBe("g-req"));
    expect(result.current.runStatus).toBe("completed");
    expect(mintWorkflowEventsTicket).not.toHaveBeenCalled();
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("shows nothing of the previous tender's gate after a tender change", async () => {
    listWorkflowRuns.mockImplementation(async (tenderId: string) =>
      tenderId === "tender-a" ? [run("run-a", "completed")] : [],
    );
    listGates.mockResolvedValue([gate("g-a", "compliance_review", "pending")]);

    const { result, rerender } = renderHook(
      ({ tenderId }: { tenderId: string }) =>
        useLatestRunGate(tenderId, "compliance_review", () => {}),
      { initialProps: { tenderId: "tender-a" } },
    );
    await waitFor(() => expect(result.current.gate?.gate_id).toBe("g-a"));

    rerender({ tenderId: "tender-b" });
    expect(result.current.gate).toBeNull();
    expect(result.current.runStatus).toBeNull();
  });

  it("says when the screen's gate opens, once, and not on the first load or a fresher read", async () => {
    listWorkflowRuns.mockResolvedValue([run("run-1", "running")]);
    getWorkflowRun.mockResolvedValue(run("run-1", "running"));
    listGates.mockResolvedValue([gate("g-req", "requirement_review", "pending")]);
    const onGateOpened = vi.fn();

    const { result } = renderHook(() =>
      useLatestRunGate("tender-1", "compliance_review", () => {}, onGateOpened),
    );
    const source = await openStream();
    await waitFor(() => expect(listGates).toHaveBeenCalledTimes(3));
    expect(onGateOpened).not.toHaveBeenCalled();

    getWorkflowRun.mockResolvedValue(run("run-1", "waiting_for_human"));
    listGates.mockResolvedValue([gate("g-com", "compliance_review", "pending")]);
    push(source);
    await waitFor(() => expect(result.current.gate?.gate_id).toBe("g-com"));
    expect(onGateOpened).toHaveBeenCalledTimes(1);

    // The same gate read again (its counts changed, say): not an opening.
    push(source);
    await waitFor(() => expect(listGates).toHaveBeenCalledTimes(5));
    await act(async () => {});
    expect(onGateOpened).toHaveBeenCalledTimes(1);
  });

  it("does not say a gate opened when it was already open on the first load", async () => {
    listWorkflowRuns.mockResolvedValue([run("run-1", "waiting_for_human")]);
    getWorkflowRun.mockResolvedValue(run("run-1", "waiting_for_human"));
    listGates.mockResolvedValue([gate("g-com", "compliance_review", "pending")]);
    const onGateOpened = vi.fn();

    const { result } = renderHook(() =>
      useLatestRunGate("tender-1", "compliance_review", () => {}, onGateOpened),
    );
    const source = await openStream();
    await waitFor(() => expect(result.current.gate?.gate_id).toBe("g-com"));
    push(source);
    await waitFor(() => expect(listGates).toHaveBeenCalledTimes(4));
    await act(async () => {});
    expect(onGateOpened).not.toHaveBeenCalled();
  });

  it("reports a failed first read", async () => {
    listWorkflowRuns.mockRejectedValue(new Error("503"));
    const onLoadError = vi.fn();

    renderHook(() => useLatestRunGate("tender-1", "finding_resolution", onLoadError));

    await waitFor(() => expect(onLoadError).toHaveBeenCalledTimes(1));
  });
});
