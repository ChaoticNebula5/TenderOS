import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { WorkflowRunResponse } from "../api/client";
import { CancelRunControl } from "./CancelRunControl";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return { ...actual, cancelWorkflowRun: vi.fn() };
});

const cancelWorkflowRun = vi.mocked(apiClient.cancelWorkflowRun);

function cancelled(id: string): WorkflowRunResponse {
  return {
    id,
    tender_id: "tender-1",
    langgraph_thread_id: "thread-1",
    status: "cancelled",
    plan_version: 1,
    prompt_bundle_version: "v1",
    started_at: "2026-01-01T00:00:00Z",
    completed_at: "2026-01-01T00:05:00Z",
    created_at: "2026-01-01T00:00:00Z",
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CancelRunControl: one question per run", () => {
  it("cancels the run the question was opened for", async () => {
    cancelWorkflowRun.mockResolvedValue(cancelled("run-A"));
    const onCancelled = vi.fn();
    render(<CancelRunControl runId="run-A" onCancelled={onCancelled} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, cancel the run" }));

    await waitFor(() => expect(onCancelled).toHaveBeenCalledWith(cancelled("run-A")));
    expect(cancelWorkflowRun).toHaveBeenCalledTimes(1);
    expect(cancelWorkflowRun).toHaveBeenCalledWith("run-A");
  });

  it("closes a question opened for run A when the followed run changes straight to run B", async () => {
    // No screen changes the followed run from one run straight to another
    // today (each path goes through "no run"); nothing enforces that. If one
    // ever does, a question opened for run A must not still be open on run
    // B, where Yes would cancel a run the user was never asked about.
    cancelWorkflowRun.mockResolvedValue(cancelled("run-B"));
    const onCancelled = vi.fn();
    const { rerender } = render(<CancelRunControl runId="run-A" onCancelled={onCancelled} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("group", { name: "Cancel this run?" })).toBeInTheDocument();

    rerender(<CancelRunControl runId="run-B" onCancelled={onCancelled} />);

    expect(screen.queryByRole("group", { name: "Cancel this run?" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeInTheDocument();

    // Asked again, now about run B: Yes cancels run B, and never run A.
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, cancel the run" }));
    await waitFor(() => expect(onCancelled).toHaveBeenCalledTimes(1));
    expect(cancelWorkflowRun).toHaveBeenCalledTimes(1);
    expect(cancelWorkflowRun).toHaveBeenCalledWith("run-B");
  });
});
