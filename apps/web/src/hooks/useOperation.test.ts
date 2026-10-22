import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import { ApiError } from "../api/client";
import type { OperationResponse } from "../api/client";
import { POLL_INTERVAL_MS, useOperation } from "./useOperation";

// Phase 4. useOperation follows an operation over SSE and polls
// GET /operations/{id} while the stream is down. EventSource is a minimal
// fake (jsdom has none), as in useEventStream.test.ts. Every value is fake.
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
  return { ...actual, getOperation: vi.fn(), mintOperationEventsTicket: vi.fn() };
});

const getOperation = vi.mocked(apiClient.getOperation);
const mintOperationEventsTicket = vi.mocked(apiClient.mintOperationEventsTicket);

function op(status: OperationResponse["status"], id = "op-1"): OperationResponse {
  return {
    operation_id: id,
    tender_id: "t1",
    kind: "redteam_run",
    status,
    error_code: status === "failed" ? "LLM_TIMEOUT" : null,
    error_message: status === "failed" ? "The model did not answer in time." : null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("useOperation", () => {
  it("follows the operation's stream, re-reads it on each event, and closes the stream once finished", async () => {
    mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
    getOperation.mockResolvedValueOnce(op("queued")).mockResolvedValueOnce(op("succeeded"));

    const { result } = renderHook(() => useOperation("op-1"));

    await waitFor(() => expect(result.current.operation?.status).toBe("queued"));
    const source = FakeEventSource.instances[0]!;
    expect(source.url).toContain("/operations/op-1/events/stream");
    expect(source.url).toContain("ticket=fake-ticket");

    act(() => source.onmessage?.(new MessageEvent("message", { data: "{}" })));
    await waitFor(() => expect(result.current.operation?.status).toBe("succeeded"));
    await waitFor(() => expect(source.readyState).toBe(FakeEventSource.CLOSED));
    expect(getOperation).toHaveBeenCalledTimes(2);
  });

  it("falls back to polling every 3s when no stream ticket can be minted, and stops at a finished status", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mintOperationEventsTicket.mockRejectedValue(new ApiError(401, { detail: "fake" }));
    getOperation
      .mockResolvedValueOnce(op("running"))
      .mockResolvedValueOnce(op("running"))
      .mockResolvedValueOnce(op("failed"));

    const { result } = renderHook(() => useOperation("op-1"));

    await waitFor(() => expect(result.current.operation?.status).toBe("running"));
    expect(FakeEventSource.instances).toHaveLength(0);
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
    await waitFor(() => expect(result.current.operation?.status).toBe("failed"));
    expect(result.current.operation?.error_message).toBe("The model did not answer in time.");

    // Finished: no further polls.
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3));
    expect(getOperation).toHaveBeenCalledTimes(3);
  });

  it("polls while a connected stream has closed and is backing off", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
    getOperation.mockResolvedValueOnce(op("running")).mockResolvedValue(op("succeeded"));

    const { result } = renderHook(() => useOperation("op-1"));
    await waitFor(() => expect(result.current.operation?.status).toBe("running"));

    const source = FakeEventSource.instances[0]!;
    act(() => {
      source.readyState = FakeEventSource.CLOSED;
      source.onerror?.();
    });
    await waitFor(() => expect(result.current.operation?.status).toBe("succeeded"));
  });

  it("does nothing without an operation id", () => {
    const { result } = renderHook(() => useOperation(null));
    expect(result.current.operation).toBeNull();
    expect(mintOperationEventsTicket).not.toHaveBeenCalled();
    expect(getOperation).not.toHaveBeenCalled();
  });
});
