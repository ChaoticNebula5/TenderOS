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

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(mintWorkflowEventsTicket).toHaveBeenCalledTimes(2);
    expect(FakeEventSource.instances[1]!.url).toContain("ticket=second-ticket");
  });
});
