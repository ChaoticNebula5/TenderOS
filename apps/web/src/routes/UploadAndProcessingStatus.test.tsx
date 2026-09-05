import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import { ApiError } from "../api/client";
import type { WorkflowRunResponse } from "../api/client";
import { UploadAndProcessingStatus } from "./UploadAndProcessingStatus";

// B17 Phase 1 - see KNOWN_LIMITATIONS.md's useEventStream entry and the
// standing pattern it's part of: useEventStream.ts already computed a real
// terminal error correctly (its own tests, useEventStream.test.ts, are
// hook-level - renderHook, no consuming component), but this was the one
// consumer and it destructured only { data }, so a dead stream was silent
// staleness on screen, not a visible error. The fix is consumer-side
// (UploadAndProcessingStatus now renders streamError); a hook-level test
// alone cannot pin that - it would keep passing even if this component
// still ignored the error entirely. This test renders the real component
// against a real useEventStream, faking only EventSource (same stand-in
// useEventStream.test.ts uses - jsdom has no real implementation) and the
// API boundary.

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
    listDocuments: vi.fn(),
    listWorkflowTasks: vi.fn(),
    getWorkflowRun: vi.fn(),
    mintWorkflowEventsTicket: vi.fn(),
  };
});

const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listDocuments = vi.mocked(apiClient.listDocuments);
const listWorkflowTasks = vi.mocked(apiClient.listWorkflowTasks);
const getWorkflowRun = vi.mocked(apiClient.getWorkflowRun);
const mintWorkflowEventsTicket = vi.mocked(apiClient.mintWorkflowEventsTicket);

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function activeRun(): WorkflowRunResponse {
  return {
    id: "run-1",
    tender_id: "tender-1",
    langgraph_thread_id: "thread-1",
    status: "running",
    plan_version: 1,
    prompt_bundle_version: "v1",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function renderAt(tenderId: string) {
  render(
    <MemoryRouter initialEntries={[`/tenders/${tenderId}`]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<UploadAndProcessingStatus />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("a dead event stream is visible, not silent", () => {
  it("surfaces useEventStream's error instead of leaving the task list quietly stale", async () => {
    listWorkflowRuns.mockResolvedValue([activeRun()]);
    listDocuments.mockResolvedValue([]);
    listWorkflowTasks.mockResolvedValue([]);
    getWorkflowRun.mockResolvedValue(activeRun());
    // A 401 minting the ticket is useEventStream's own terminal case (see
    // useEventStream.test.ts's identical scenario) - deterministic and
    // immediate, no backoff wait needed to reach a real error state.
    mintWorkflowEventsTicket.mockRejectedValue(new ApiError(401, { detail: "expired session" }));

    renderAt("tender-1");

    // A 401 on the very first mint attempt means useEventStream's own
    // openStream returns before ever calling refetch() (client.ts read
    // directly: the REST fetch only happens after a successful mint) - so
    // there is no "last-known" task data to have survived here, and the
    // list correctly renders empty rather than the "Waiting for tasks to
    // start..." placeholder (which requires `tasks !== null`, i.e. at
    // least one real fetch attempt). What this scenario actually proves is
    // narrower and still the point: the warning itself renders, where
    // before this fix nothing would have.
    expect(
      await screen.findByText(/Live updates are currently unavailable/),
    ).toBeInTheDocument();
    expect(mintWorkflowEventsTicket).toHaveBeenCalled();
  });

  it("shows no warning while the stream is healthy", async () => {
    listWorkflowRuns.mockResolvedValue([activeRun()]);
    listDocuments.mockResolvedValue([]);
    listWorkflowTasks.mockResolvedValue([]);
    getWorkflowRun.mockResolvedValue(activeRun());
    mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });

    renderAt("tender-1");

    expect(await screen.findByText("Waiting for tasks to start...")).toBeInTheDocument();
    expect(screen.queryByText(/Live updates are currently unavailable/)).not.toBeInTheDocument();
  });
});
