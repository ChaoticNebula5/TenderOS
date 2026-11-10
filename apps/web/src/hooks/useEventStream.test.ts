import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import { ApiError } from "../api/client";
import { useEventStream } from "./useEventStream";

// B15: EventSource isn't implemented in jsdom - a minimal fake standing in
// for exactly the two things this hook actually uses (readyState, .close(),
// onmessage/onerror callbacks, and the URL it was constructed with).
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
  return { ...actual, mintWorkflowEventsTicket: vi.fn() };
});

const mintWorkflowEventsTicket = vi.mocked(apiClient.mintWorkflowEventsTicket);

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const fetcher = async () => ({ ok: true });

describe("useEventStream", () => {
  it("mints a ticket and opens EventSource with it, not the raw access token", async () => {
    mintWorkflowEventsTicket.mockResolvedValue({ ticket: "real-ticket", expires_in_seconds: 30 });

    renderHook(() => useEventStream(fetcher, "run-1", true));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const url = FakeEventSource.instances[0]!.url;
    expect(url).toContain("ticket=real-ticket");
    expect(url).not.toContain("access_token");
  });

  it("reads the resource again once the stream opens (plan item 54)", async () => {
    // The read sent before the stream opened answered with the old state;
    // the change landed before the server fixed the stream's starting set,
    // so no message will ever announce it. Only the read on open shows it.
    mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
    const states = ["before the change", "after the change"];
    let reads = 0;
    const changing = async () => states[Math.min(reads++, states.length - 1)];

    const { result } = renderHook(() => useEventStream(changing, "run-6", true));

    await waitFor(() => expect(result.current.data).toBe("before the change"));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    FakeEventSource.instances[0]!.onopen?.();
    await waitFor(() => expect(result.current.data).toBe("after the change"));
  });

  it("a 401 minting the ticket surfaces as error and does not retry", async () => {
    mintWorkflowEventsTicket.mockRejectedValue(new ApiError(401, { detail: "expired session" }));

    const { result } = renderHook(() => useEventStream(fetcher, "run-2", true));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(mintWorkflowEventsTicket).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("a terminal EventSource failure mints a fresh ticket and opens a new connection", async () => {
    mintWorkflowEventsTicket
      .mockResolvedValueOnce({ ticket: "first-ticket", expires_in_seconds: 30 })
      .mockResolvedValueOnce({ ticket: "second-ticket", expires_in_seconds: 30 });

    renderHook(() => useEventStream(fetcher, "run-3", true));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const first = FakeEventSource.instances[0]!;

    // Simulate the shape a reused single-use ticket actually produces:
    // EventSource gives up on its own (readyState CLOSED) rather than
    // auto-retrying, since the server rejects it outright.
    first.readyState = FakeEventSource.CLOSED;
    first.onerror?.();

    // No new connection appears in the same tick - the whole point of the
    // backoff fix is that this reconnect is no longer instant.
    expect(FakeEventSource.instances).toHaveLength(1);

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 2000 });
    expect(mintWorkflowEventsTicket).toHaveBeenCalledTimes(2);
    expect(FakeEventSource.instances[1]!.url).toContain("ticket=second-ticket");
  });

  it("a drop the browser would retry itself is closed and reconnected with a fresh ticket", async () => {
    // Phase 4 plan item 34. An error with readyState still CONNECTING is
    // EventSource about to reconnect on its own, to the same URL, with a
    // ticket the server has already consumed. The hook must close it and
    // open its own connection with a new ticket instead.
    mintWorkflowEventsTicket
      .mockResolvedValueOnce({ ticket: "first-ticket", expires_in_seconds: 30 })
      .mockResolvedValueOnce({ ticket: "second-ticket", expires_in_seconds: 30 });

    renderHook(() => useEventStream(fetcher, "run-5", true));

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const first = FakeEventSource.instances[0]!;
    first.onopen?.();
    first.readyState = FakeEventSource.CONNECTING;
    first.onerror?.();

    expect(first.readyState).toBe(FakeEventSource.CLOSED);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 2000 });
    expect(mintWorkflowEventsTicket).toHaveBeenCalledTimes(2);
    expect(FakeEventSource.instances[1]!.url).toContain("ticket=second-ticket");
  });

  it("backs off exponentially on repeated terminal failures and resets after a real onopen", async () => {
    vi.useFakeTimers();
    try {
      mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });

      renderHook(() => useEventStream(fetcher, "run-4", true));
      await vi.advanceTimersByTimeAsync(0);
      expect(FakeEventSource.instances).toHaveLength(1);

      // First terminal failure: backoff is 1000ms (2^0), not instant.
      FakeEventSource.instances[0]!.readyState = FakeEventSource.CLOSED;
      FakeEventSource.instances[0]!.onerror?.();
      await vi.advanceTimersByTimeAsync(999);
      expect(FakeEventSource.instances).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(2);
      expect(FakeEventSource.instances).toHaveLength(2);

      // A second consecutive failure, with no onopen in between, doubles
      // the backoff to ~2000ms (2^1) instead of resetting to ~1000ms.
      FakeEventSource.instances[1]!.readyState = FakeEventSource.CLOSED;
      FakeEventSource.instances[1]!.onerror?.();
      await vi.advanceTimersByTimeAsync(1999);
      expect(FakeEventSource.instances).toHaveLength(2);
      await vi.advanceTimersByTimeAsync(2);
      expect(FakeEventSource.instances).toHaveLength(3);

      // A real onopen on the third connection resets the counter - the
      // next failure backs off from ~1000ms again, not ~4000ms.
      FakeEventSource.instances[2]!.onopen?.();
      FakeEventSource.instances[2]!.readyState = FakeEventSource.CLOSED;
      FakeEventSource.instances[2]!.onerror?.();
      await vi.advanceTimersByTimeAsync(999);
      expect(FakeEventSource.instances).toHaveLength(3);
      await vi.advanceTimersByTimeAsync(2);
      expect(FakeEventSource.instances).toHaveLength(4);
    } finally {
      vi.useRealTimers();
    }
  });
});
