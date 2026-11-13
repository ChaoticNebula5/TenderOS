import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import { ApiError } from "../api/client";
import type { DocumentResponse, WorkflowRunResponse, WorkflowTaskResponse } from "../api/client";
import { TenderLayout } from "../shell/TenderLayout";
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
    uploadDocument: vi.fn(),
    createWorkflowRun: vi.fn(),
    listGates: vi.fn(),
    cancelWorkflowRun: vi.fn(),
  };
});

const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listDocuments = vi.mocked(apiClient.listDocuments);
const listWorkflowTasks = vi.mocked(apiClient.listWorkflowTasks);
const getWorkflowRun = vi.mocked(apiClient.getWorkflowRun);
const mintWorkflowEventsTicket = vi.mocked(apiClient.mintWorkflowEventsTicket);
const uploadDocument = vi.mocked(apiClient.uploadDocument);
const createWorkflowRun = vi.mocked(apiClient.createWorkflowRun);
const listGates = vi.mocked(apiClient.listGates);
const cancelWorkflowRun = vi.mocked(apiClient.cancelWorkflowRun);

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
    expect(await screen.findByText(/Live updates are currently unavailable/)).toBeInTheDocument();
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

// Restyle batch 3. Fixture values are fake.
function parsedDocument(): DocumentResponse {
  return {
    id: "doc-1",
    filename: "rfp.pdf",
    doc_type: "rfp",
    parse_status: "parsed",
  } as DocumentResponse;
}

function task(overrides: Partial<WorkflowTaskResponse>): WorkflowTaskResponse {
  return {
    id: "task-1",
    workflow_run_id: "run-1",
    task_key: "k",
    task_type: "requirement_extraction",
    assigned_agent: "a",
    status: "running",
    priority: 1,
    attempt_count: 0,
    max_attempts: 3,
    error_code: null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function renderInTenderLayout(tenderId: string) {
  return render(
    <MemoryRouter initialEntries={[`/tenders/${tenderId}`]}>
      <Routes>
        <Route path="/tenders/:tenderId" element={<TenderLayout />}>
          <Route index element={<UploadAndProcessingStatus />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("restyle batch 3: processing screen", () => {
  it("keeps the parse cell's DOM text the backend word (full-flow.spec.ts:558-566 compares it to 'parsed')", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    listDocuments.mockResolvedValue([parsedDocument()]);
    const { container } = render(
      <MemoryRouter initialEntries={["/tenders/tender-1"]}>
        <Routes>
          <Route path="/tenders/:tenderId" element={<UploadAndProcessingStatus />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("rfp.pdf");
    const cell = container.querySelector("table tbody tr td:nth-child(3)");
    expect(cell?.textContent).toBe("parsed");
    expect(cell?.querySelector(".parse-status .status-badge--ready")).not.toBeNull();
  });

  it("does not claim 'no documents' before the documents have loaded", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    let resolveDocuments: (docs: DocumentResponse[]) => void = () => {};
    listDocuments.mockReturnValue(
      new Promise<DocumentResponse[]>((r) => {
        resolveDocuments = r;
      }),
    );
    renderAt("tender-1");

    expect(screen.queryByText("No documents uploaded yet")).toBeNull();
    resolveDocuments([]);
    expect(await screen.findByText("No documents uploaded yet")).toBeInTheDocument();
  });

  it("no longer renders the old link row - the workflow nav replaces it", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    listDocuments.mockResolvedValue([]);
    renderAt("tender-1");

    await screen.findByText("No documents uploaded yet");
    expect(screen.queryAllByRole("link")).toEqual([]);
  });

  it("lists tasks with a humanized type and a status badge", async () => {
    listWorkflowRuns.mockResolvedValue([activeRun()]);
    listDocuments.mockResolvedValue([]);
    listWorkflowTasks.mockResolvedValue([
      task({ id: "t1", task_type: "requirement_extraction", status: "completed" }),
      task({ id: "t2", task_type: "compliance_review", status: "failed" }),
    ]);
    getWorkflowRun.mockResolvedValue(activeRun());
    mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
    renderAt("tender-1");

    expect(await screen.findByText("Requirement extraction")).toBeInTheDocument();
    expect(screen.getByText("Done")).toHaveClass("status-badge--ready");
    expect(screen.getByText("Compliance review")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toHaveClass("status-badge--failed");
    expect(screen.queryByText(/requirement_extraction/)).toBeNull();
  });

  it("reports the last run's status to the tender bar, from the run list it already fetches", async () => {
    listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "failed" }]);
    listDocuments.mockResolvedValue([]);
    const { container } = renderInTenderLayout("tender-1");

    await screen.findByText("No documents uploaded yet");
    // The bar updates from an effect, one commit after the screen: wait for it.
    await waitFor(() =>
      expect(container.querySelector(".tender-bar .status-badge")).toHaveTextContent("Failed"),
    );
    const barBadge = container.querySelector(".tender-bar .status-badge");
    expect(barBadge?.closest("main")).toBeNull();
    // No new call: the run list and documents, as before, and no run fetch.
    expect(listWorkflowRuns).toHaveBeenCalledTimes(1);
    expect(getWorkflowRun).not.toHaveBeenCalled();
  });
});

// CI run 39983426118 (Dependabot #33): the E2E uploaded a document on a
// freshly opened screen, the upload's row appeared, and the screen's first
// documents read, sent before the upload and answered after it, replaced
// the list with its empty picture. The table stayed at 0 rows and
// full-flow.spec.ts:540 timed out. Here the first read is held until the
// upload has landed, then answers with the list as it was when it started.
describe("an upload that lands before the first documents read answers", () => {
  it("keeps the uploaded row, then shows the server's list", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    const uploaded = { ...parsedDocument(), id: "doc-new", filename: "rfp.pdf" };
    let answerFirstRead: (docs: DocumentResponse[]) => void = () => {};
    let answerReread: (docs: DocumentResponse[]) => void = () => {};
    listDocuments
      .mockReturnValueOnce(
        new Promise<DocumentResponse[]>((r) => {
          answerFirstRead = r;
        }),
      )
      .mockReturnValueOnce(
        new Promise<DocumentResponse[]>((r) => {
          answerReread = r;
        }),
      );
    uploadDocument.mockResolvedValue(uploaded);
    const { container } = render(
      <MemoryRouter initialEntries={["/tenders/tender-1"]}>
        <Routes>
          <Route path="/tenders/:tenderId" element={<UploadAndProcessingStatus />} />
        </Routes>
      </MemoryRouter>,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "rfp.pdf")] } });
    expect(await screen.findByText("rfp.pdf")).toBeInTheDocument();

    // The screen reads the list once more after the upload, so what it
    // shows ends as the server's state, not only the upload's own answer.
    // That read is held too: the first read answers before it, as in CI.
    await waitFor(() => expect(listDocuments).toHaveBeenCalledTimes(2));
    answerFirstRead([]);
    await new Promise((r) => setTimeout(r, 0));

    // The race itself: the late, older answer must not remove the row.
    expect(container.querySelectorAll("table tbody tr")).toHaveLength(1);

    answerReread([{ ...uploaded, parse_status: "parsing" }]);
    await screen.findByText("parsing");
    expect(container.querySelectorAll("table tbody tr")).toHaveLength(1);
  });
});

// Phase 4 plan item 43: the server allows one active run per tender. A
// second Start processing (another tab, or a click from a screen that had
// not seen the first run) gets a 409 WORKFLOW_RUN_ACTIVE naming that run.
describe("Start processing when a run is already active", () => {
  function activeRunConflict(): ApiError {
    return new ApiError(409, {
      error: {
        code: "WORKFLOW_RUN_ACTIVE",
        message: "This tender already has a workflow run in progress.",
        details: { active_run_id: "run-1" },
      },
    });
  }

  it("shows the active run as if this click had started it, with no error", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    listDocuments.mockResolvedValue([parsedDocument()]);
    createWorkflowRun.mockRejectedValue(activeRunConflict());
    getWorkflowRun.mockResolvedValue(activeRun());
    listWorkflowTasks.mockResolvedValue([task({})]);
    mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
    renderAt("tender-1");

    fireEvent.click(await screen.findByRole("button", { name: "Start processing" }));

    expect(await screen.findByText("Requirement extraction")).toBeInTheDocument();
    expect(getWorkflowRun).toHaveBeenCalledWith("run-1");
    expect(listWorkflowTasks).toHaveBeenCalledWith("run-1");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Start processing" })).toBeNull();
  });

  it("still explains a 409 for documents that are not parsed yet", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    listDocuments.mockResolvedValue([parsedDocument()]);
    createWorkflowRun.mockRejectedValue(
      new ApiError(409, { error: { code: "DOCUMENTS_NOT_READY", message: "x" } }),
    );
    renderAt("tender-1");

    fireEvent.click(await screen.findByRole("button", { name: "Start processing" }));

    expect(
      await screen.findByText("All documents must finish parsing before processing can start."),
    ).toBeInTheDocument();
    expect(getWorkflowRun).not.toHaveBeenCalled();
  });
});

// Plan item 19: a failed run says why, in one sentence under "Last run",
// when the backend recorded a reason (WorkflowRunResponse.error_code).
describe("why the last run failed", () => {
  const sentences: [NonNullable<WorkflowRunResponse["error_code"]>, string][] = [
    [
      "WORKER_LOST",
      "The last run stopped because the background job processing it was lost. Start processing again to run it from the beginning.",
    ],
    [
      "DISPATCH_FAILED",
      "The last run stopped before its next step could start. Start processing again to run it from the beginning.",
    ],
    [
      "JOB_TIMEOUT",
      "The last run stopped because one step ran past its time limit. Start processing again to run it from the beginning.",
    ],
  ];

  it.each(sentences)("shows the %s sentence under Last run", async (code, sentence) => {
    listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "failed", error_code: code }]);
    listDocuments.mockResolvedValue([]);
    renderAt("tender-1");

    expect(await screen.findByText(sentence)).toBeInTheDocument();
    expect(screen.getByText("Last run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start processing" })).toBeInTheDocument();
  });

  it("adds nothing for a failed run with no recorded reason", async () => {
    listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "failed", error_code: null }]);
    listDocuments.mockResolvedValue([]);
    const { container } = render(
      <MemoryRouter initialEntries={["/tenders/tender-1"]}>
        <Routes>
          <Route path="/tenders/:tenderId" element={<UploadAndProcessingStatus />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Last run")).toBeInTheDocument();
    expect(container.querySelector(".processing__last-run-reason")).toBeNull();
  });

  it("shows the reason when the open run ends failed on the stream", async () => {
    const sentence =
      "The last run stopped because the background job processing it was lost. Start processing again to run it from the beginning.";
    const failedRun: WorkflowRunResponse = {
      ...activeRun(),
      status: "failed",
      error_code: "WORKER_LOST",
    };
    // Once the stream reports the run failed, the screen clears the run id
    // and lists the tender's runs again. A real server answers that second
    // list with the failed run; the first version of this test kept
    // answering "running", so the screen followed the run back to its task
    // list and the sentence was gone again (CI run 41811601302). The second
    // answer is held back so the sentence is first checked with the code
    // from the stream alone.
    let answerReread: (runs: WorkflowRunResponse[]) => void = () => {};
    listWorkflowRuns
      .mockResolvedValueOnce([activeRun()])
      .mockReturnValueOnce(new Promise((resolve) => (answerReread = resolve)));
    listDocuments.mockResolvedValue([]);
    listWorkflowTasks.mockResolvedValue([]);
    getWorkflowRun.mockResolvedValue(failedRun);
    mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
    renderAt("tender-1");

    // The second list is sent only after the screen has taken the stream's
    // failed run and rendered "Last run" for it.
    await waitFor(() => expect(listWorkflowRuns).toHaveBeenCalledTimes(2));
    expect(screen.getByText(sentence)).toBeInTheDocument();

    await act(async () => answerReread([failedRun]));
    expect(screen.getByText(sentence)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start processing" })).toBeInTheDocument();
    expect(listWorkflowRuns).toHaveBeenCalledTimes(2);
  });
});

// Plan item 71: a reviewer's Request changes ends the run (terminal status
// changes_requested). The page names the gate, shows the notes, and offers
// Start processing again.
function decidedGate(
  status: "approved" | "changes_requested",
  gateType: "requirement_review" | "compliance_review",
  notes: string | null,
): apiClient.GateResponse {
  return {
    gate_id: `gate-${gateType}`,
    gate_type: gateType,
    workflow_run_id: "run-1",
    status,
    targets: [],
    summary: {},
    allowed_decisions: ["approve", "reject", "request_changes"],
    decision_payload: { decision: status === "approved" ? "approve" : "request_changes", notes },
    decided_by: null,
    requested_at: "2026-01-01T00:00:00Z",
    decided_at: "2026-01-01T01:00:00Z",
  } as unknown as apiClient.GateResponse;
}

describe("a run ended by Request changes", () => {
  it("names the gate, shows the reviewer's notes, and offers Start processing", async () => {
    listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "changes_requested" }]);
    listDocuments.mockResolvedValue([]);
    listGates.mockResolvedValue([
      decidedGate("approved", "requirement_review", null),
      decidedGate("changes_requested", "compliance_review", "Recheck clause 4.2."),
    ]);
    renderAt("tender-1");

    expect(
      await screen.findByText("Changes were requested at the Compliance review gate."),
    ).toBeInTheDocument();
    expect(screen.getByText("Changes requested")).toHaveClass("status-badge--blocked");
    expect(screen.getByText("Recheck clause 4.2.", { exact: false })).toHaveClass(
      "processing__last-run-notes",
    );
    expect(listGates).toHaveBeenCalledWith("run-1");
    expect(screen.getByRole("button", { name: "Start processing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
  });

  it("still says what happened when the gates cannot be read", async () => {
    listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "changes_requested" }]);
    listDocuments.mockResolvedValue([]);
    listGates.mockRejectedValue(new ApiError(500, {}));
    renderAt("tender-1");

    expect(
      await screen.findByText("Changes were requested at one of the run's review gates."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start processing" })).toBeInTheDocument();
  });

  it("reads no gates for any other outcome", async () => {
    listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "completed" }]);
    listDocuments.mockResolvedValue([]);
    renderAt("tender-1");

    expect(await screen.findByText("Last run")).toBeInTheDocument();
    expect(listGates).not.toHaveBeenCalled();
  });
});

// Plan item 72: cancel the active run, confirmed inside the page.
function renderWithActiveRun() {
  listWorkflowRuns.mockResolvedValue([activeRun()]);
  listDocuments.mockResolvedValue([]);
  listWorkflowTasks.mockResolvedValue([]);
  getWorkflowRun.mockResolvedValue(activeRun());
  mintWorkflowEventsTicket.mockResolvedValue({ ticket: "t", expires_in_seconds: 30 });
  renderAt("tender-1");
}

describe("cancelling the active run", () => {
  it("asks inside the page, never through window.confirm, and cancels once", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    renderWithActiveRun();
    // Once the run has ended the page re-reads the tender's runs (its
    // run-lookup effect); the server then lists this run as cancelled.
    cancelWorkflowRun.mockImplementation(async () => {
      listWorkflowRuns.mockResolvedValue([{ ...activeRun(), status: "cancelled" }]);
      return { ...activeRun(), status: "cancelled" };
    });

    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    const region = screen.getByRole("group", { name: "Cancel this run?" });
    expect(region).toHaveTextContent("Work already done is kept.");
    // Focus lands on the safe choice.
    await waitFor(() => expect(screen.getByRole("button", { name: "Keep running" })).toHaveFocus());
    expect(cancelWorkflowRun).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Yes, cancel the run" }));

    expect(await screen.findByText("Cancelled")).toHaveClass("status-badge--neutral");
    expect(cancelWorkflowRun).toHaveBeenCalledTimes(1);
    expect(cancelWorkflowRun).toHaveBeenCalledWith("run-1");
    expect(screen.getByText("Last run")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start processing" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Cancel this run?" })).not.toBeInTheDocument();
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("Keep running and Escape close the question without cancelling", async () => {
    renderWithActiveRun();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep running" }));
    expect(screen.queryByRole("group", { name: "Cancel this run?" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    fireEvent.keyDown(screen.getByRole("group", { name: "Cancel this run?" }), { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Cancel this run?" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel run" })).toHaveFocus());
    expect(cancelWorkflowRun).not.toHaveBeenCalled();
  });

  it("says when the role may not cancel, and keeps the question open", async () => {
    cancelWorkflowRun.mockRejectedValue(new ApiError(403, { detail: "Forbidden" }));
    renderWithActiveRun();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, cancel the run" }));

    expect(await screen.findByText("Your role can't cancel runs.")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Cancel this run?" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start processing" })).not.toBeInTheDocument();
  });

  it("says when the cancel failed for any other reason", async () => {
    cancelWorkflowRun.mockRejectedValue(new ApiError(503, {}));
    renderWithActiveRun();

    fireEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, cancel the run" }));

    expect(await screen.findByText("Failed to cancel the run.")).toBeInTheDocument();
  });
});
