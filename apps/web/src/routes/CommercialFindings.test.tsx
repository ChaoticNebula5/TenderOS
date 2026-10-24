import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
  OperationResponse,
  PricingDocumentResponse,
  PricingFindingResponse,
} from "../api/client";
import { CommercialFindings } from "./CommercialFindings";

// B17 Phase 1 - see KNOWN_LIMITATIONS.md, "[owner: B17] CommercialFindings.
// tsx's handleValidate hardcodes its own stale-write guard to never fire."
// This is the fix's own regression test: without it, tender A's re-fetched
// findings could render under tender B after a navigate-during-request race
// - exactly the "confirmed real... not yet reproduced live" gap that entry
// named as still open. Deterministic: validatePricingDocument's own promise
// is held open under test control, so the race is driven directly rather
// than hoped for via timing.
//
// Phase 4 Stage 2: Re-validate starts a tender-wide async operation (202)
// and follows it over SSE (jsdom has no EventSource; a minimal fake records
// the streams opened). The findings are re-read once it has finished,
// before the buttons are released, and an operation already active when the
// screen loads is followed the same way. Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listPricingDocuments: vi.fn(),
    listPricingFindings: vi.fn(),
    listWorkflowRuns: vi.fn(),
    listGates: vi.fn(),
    validatePricingDocument: vi.fn(),
    resolvePricingFinding: vi.fn(),
    getActiveOperation: vi.fn(),
    getOperation: vi.fn(),
    mintOperationEventsTicket: vi.fn(),
  };
});

const listPricingDocuments = vi.mocked(apiClient.listPricingDocuments);
const listPricingFindings = vi.mocked(apiClient.listPricingFindings);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const validatePricingDocument = vi.mocked(apiClient.validatePricingDocument);
const resolvePricingFinding = vi.mocked(apiClient.resolvePricingFinding);
const getActiveOperation = vi.mocked(apiClient.getActiveOperation);
const getOperation = vi.mocked(apiClient.getOperation);
const mintOperationEventsTicket = vi.mocked(apiClient.mintOperationEventsTicket);

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

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  getActiveOperation.mockResolvedValue(null);
  mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function operation(status: OperationResponse["status"]): OperationResponse {
  return {
    operation_id: "op-tender-a",
    tender_id: "tender-a",
    kind: "commercial_validation",
    status,
    error_code: status === "failed" ? "LLM_TIMEOUT" : null,
    error_message: status === "failed" ? "The model did not answer in time." : null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

// The operation's stream pushes a wake-up; the hook then re-reads it.
function pushStatus() {
  act(() => FakeEventSource.instances.at(-1)!.onmessage?.(new MessageEvent("message")));
}

function doc(tenderId: string): PricingDocumentResponse {
  return {
    id: `doc-${tenderId}`,
    tender_id: tenderId,
    document_id: `source-doc-${tenderId}`,
    sheet_name: "Pricing",
    currency: "USD",
  };
}

function finding(tenderId: string, note: string): PricingFindingResponse {
  return {
    id: `finding-${tenderId}`,
    tender_id: tenderId,
    finding_type: "arithmetic_mismatch",
    item_a_ref: null,
    item_b_ref: null,
    delta: null,
    severity: "high",
    note,
    resolved: false,
    created_at: "2026-01-01T00:00:00Z",
  };
}

// Keeps CommercialFindings mounted across the tenderId change, the same way
// App.tsx's own routing does (no per-tender key) - remounting a fresh
// instance on navigation would make the stale-write race this test exists
// to drive structurally impossible to trigger, defeating the point.
function Harness() {
  return (
    <MemoryRouter initialEntries={["/tenders/tender-a/commercial"]}>
      <Link to="/tenders/tender-b/commercial">switch to tender-b</Link>
      <Routes>
        <Route path="/tenders/:tenderId/commercial" element={<CommercialFindings />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("handleValidate's stale-write guard", () => {
  it("a validate started on tender A does not overwrite tender B's findings once the user has navigated away", async () => {
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listGates.mockResolvedValue([]);
    listPricingDocuments.mockImplementation((tenderId) =>
      Promise.resolve(tenderId === "tender-a" ? [doc("tender-a")] : []),
    );
    listPricingFindings.mockImplementation((tenderId) =>
      Promise.resolve([
        finding(tenderId, tenderId === "tender-a" ? "tender A finding" : "tender B finding"),
      ]),
    );
    // Held open until the test explicitly resolves it - this is what lets
    // the navigation below land WHILE handleValidate is still in flight,
    // driving the exact race the guard exists for, rather than hoping a
    // real timing gap shows up.
    let resolveValidate: ((op: OperationResponse) => void) | undefined;
    validatePricingDocument.mockImplementation(
      () =>
        new Promise<OperationResponse>((resolve) => {
          resolveValidate = resolve;
        }),
    );

    render(<Harness />);

    expect(await screen.findByText("tender A finding")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Re-validate" }));
    expect(validatePricingDocument).toHaveBeenCalledTimes(1);

    // Navigate away while validatePricingDocument is still pending - the
    // component stays mounted (same Route, only :tenderId changed), so its
    // own mount effect re-fetches and shows tender B's findings while
    // handleValidate's tender-A call is still in flight underneath it.
    await user.click(screen.getByText("switch to tender-b"));
    expect(await screen.findByText("tender B finding")).toBeInTheDocument();

    // Now let the stale, tender-A-scoped POST resolve with its operation.
    // Stage 2: the screen does not follow an operation started for a tender
    // it has left (useTenderOperation's guard), so no stream opens for it
    // and nothing re-reads tender A's findings onto tender B's screen.
    resolveValidate?.(operation("queued"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mintOperationEventsTicket).not.toHaveBeenCalled();
    expect(listPricingFindings).toHaveBeenCalledTimes(2);
    expect(screen.getByText("tender B finding")).toBeInTheDocument();
    expect(screen.queryByText("tender A finding")).not.toBeInTheDocument();
  });

  it("a finished operation's re-read of tender A, still in flight after navigating away, does not land on tender B", async () => {
    // Stage 2's own stale-write case: the operation finishes, the screen
    // starts re-reading tender A's findings, and the user moves to tender B
    // before that read answers. handleFinished's guard discards it.
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listGates.mockResolvedValue([]);
    listPricingDocuments.mockImplementation((tenderId) =>
      Promise.resolve(tenderId === "tender-a" ? [doc("tender-a")] : []),
    );
    let resolveStaleRead: ((rows: PricingFindingResponse[]) => void) | undefined;
    listPricingFindings
      .mockResolvedValueOnce([finding("tender-a", "tender A finding")])
      .mockImplementationOnce(
        () =>
          new Promise<PricingFindingResponse[]>((resolve) => {
            resolveStaleRead = resolve;
          }),
      )
      .mockResolvedValueOnce([finding("tender-b", "tender B finding")]);
    validatePricingDocument.mockResolvedValue(operation("queued"));
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));

    render(<Harness />);
    expect(await screen.findByText("tender A finding")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Re-validate" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    pushStatus();
    await waitFor(() => expect(listPricingFindings).toHaveBeenCalledTimes(2));

    await user.click(screen.getByText("switch to tender-b"));
    expect(await screen.findByText("tender B finding")).toBeInTheDocument();

    resolveStaleRead?.([finding("tender-a", "stale tender A finding")]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("tender B finding")).toBeInTheDocument();
    expect(screen.queryByText("stale tender A finding")).not.toBeInTheDocument();
  });

  it("a validate started on tender A that REJECTS after navigating away does not paint its error onto tender B's screen", async () => {
    // CodeRabbit-caught gap: the success path above was guarded, but the
    // catch block's setError call was not - a stale rejection would show
    // "Validation failed" on tender B's screen even though tender B's own
    // validate was never touched.
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listGates.mockResolvedValue([]);
    listPricingDocuments.mockImplementation((tenderId) =>
      Promise.resolve(tenderId === "tender-a" ? [doc("tender-a")] : []),
    );
    listPricingFindings.mockImplementation((tenderId) =>
      Promise.resolve([
        finding(tenderId, tenderId === "tender-a" ? "tender A finding" : "tender B finding"),
      ]),
    );
    let rejectValidate: ((err: unknown) => void) | undefined;
    validatePricingDocument.mockImplementation(
      () =>
        new Promise<OperationResponse>((_resolve, reject) => {
          rejectValidate = reject;
        }),
    );

    render(<Harness />);

    expect(await screen.findByText("tender A finding")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Re-validate" }));
    expect(validatePricingDocument).toHaveBeenCalledTimes(1);

    await user.click(screen.getByText("switch to tender-b"));
    expect(await screen.findByText("tender B finding")).toBeInTheDocument();

    // Now let the stale, tender-A-scoped validate actually reject.
    rejectValidate?.(new Error("stale failure"));

    // Give the rejected promise's .catch a turn to run before asserting its
    // absence - waitFor retries until either the guard holds (no alert ever
    // appears) or the timeout proves it doesn't.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("tender B finding")).toBeInTheDocument();
  });
});

// Restyle batch 4. Pins the DOM contract full-flow.spec.ts reads on this
// screen, and the finding badges (DESIGN.md:296-299). Fake fixture values.
describe("CommercialFindings (restyle batch 4)", () => {
  function renderSingle() {
    return render(
      <MemoryRouter initialEntries={["/tenders/tender-a/commercial"]}>
        <Routes>
          <Route path="/tenders/:tenderId/commercial" element={<CommercialFindings />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  function typed(id: string, overrides: Partial<PricingFindingResponse>): PricingFindingResponse {
    return { ...finding("tender-a", `note ${id}`), id, ...overrides };
  }

  it("keeps the findings table at index 1 even with no pricing documents (full-flow.spec.ts:922, :1058)", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([]);
    listPricingFindings.mockResolvedValue([typed("f1", { finding_type: "wrong_total" })]);
    const { container } = renderSingle();

    await screen.findByText("note f1");
    const tables = container.querySelectorAll("table");
    expect(tables).toHaveLength(2);
    expect(tables[0].querySelectorAll("tbody tr")).toHaveLength(0);
    expect(tables[1].querySelectorAll("tbody tr")).toHaveLength(1);
    expect(screen.getByText("No pricing documents").closest("table")).toBeNull();
    expect(within(tables[1]).getByText("Wrong total")).toBeInTheDocument();
  });

  it("shows open findings by severity and resolved ones as Resolved, with Resolve only on open rows, removed once resolved (full-flow.spec.ts:1059-1076)", async () => {
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([doc("tender-a")]);
    listPricingFindings.mockResolvedValue([
      typed("f1", { severity: "critical" }),
      typed("f2", { severity: "major" }),
      typed("f3", { severity: "minor", resolved: true }),
    ]);
    resolvePricingFinding.mockResolvedValue(typed("f1", { severity: "critical", resolved: true }));
    const { container } = renderSingle();

    await screen.findByText("note f1");
    const findingsTable = container.querySelectorAll("table")[1] as HTMLElement;
    const rows = findingsTable.querySelectorAll("tbody tr");
    expect(within(rows[0] as HTMLElement).getByText("Critical")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("Major")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByText("Resolved")).toBeInTheDocument();
    expect(within(findingsTable).getAllByRole("button", { name: "Resolve" })).toHaveLength(2);
    expect(screen.getByText("3 findings, 2 open")).toBeInTheDocument();

    await user.click(within(rows[0] as HTMLElement).getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(within(findingsTable).getAllByRole("button", { name: "Resolve" })).toHaveLength(1),
    );
    expect(within(rows[0] as HTMLElement).getByText("Resolved")).toBeInTheDocument();
  });

  it("shows no money: delta is not rendered and nothing carries a rupee sign", async () => {
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([{ ...doc("tender-a"), currency: null }]);
    listPricingFindings.mockResolvedValue([typed("f1", { delta: 12175.5 })]);
    const { container } = renderSingle();

    await screen.findByText("note f1");
    expect(container.textContent).not.toContain("12175");
    expect(container.textContent).not.toContain("₹");
    expect(screen.getByText("Not stated")).toBeInTheDocument();
  });

  it("keeps every Re-validate busy, not only the clicked one, until the tender-wide operation finishes and the findings are re-read", async () => {
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([doc("tender-a"), { ...doc("tender-a"), id: "doc-2" }]);
    let resolveReread!: (rows: PricingFindingResponse[]) => void;
    listPricingFindings
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(new Promise((r) => (resolveReread = r)));
    validatePricingDocument.mockResolvedValue(operation("queued"));
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    renderSingle();

    const buttons = await screen.findAllByRole("button", { name: /^Re-validat/ });
    await user.click(buttons[0]);
    expect(validatePricingDocument).toHaveBeenCalledWith("doc-tender-a");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    // Accepted and running: one operation re-validates every document, so
    // every row is busy, the same as after a reload. Nothing re-read yet.
    expect(buttons[0]).toHaveAttribute("aria-busy", "true");
    expect(buttons[1]).toHaveAttribute("aria-busy", "true");
    // Visible, not only in the DOM: the quiet button has no other busy look.
    expect(buttons[0]).toHaveTextContent("Re-validating...");
    expect(buttons[1]).toHaveTextContent("Re-validating...");
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1]).toBeDisabled();
    expect(listPricingFindings).toHaveBeenCalledTimes(1);

    // Finished: the findings are re-read, and the buttons stay busy until
    // that read has answered.
    pushStatus();
    await waitFor(() => expect(listPricingFindings).toHaveBeenCalledTimes(2));
    expect(buttons[0]).toBeDisabled();
    expect(buttons[0]).toHaveAttribute("aria-busy", "true");

    resolveReread([typed("f9", {})]);
    await screen.findByText("note f9");
    await waitFor(() => expect(buttons[0]).toBeEnabled());
    expect(buttons[1]).toBeEnabled();
    expect(buttons[0]).not.toHaveAttribute("aria-busy");
    expect(buttons[1]).not.toHaveAttribute("aria-busy");
    expect(buttons[0]).toHaveTextContent("Re-validate");
  });

  it("shows a failed re-validation's own message and frees the buttons", async () => {
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([doc("tender-a")]);
    listPricingFindings.mockResolvedValue([]);
    validatePricingDocument.mockResolvedValue(operation("queued"));
    getOperation.mockResolvedValue(operation("failed"));
    renderSingle();

    await user.click(await screen.findByRole("button", { name: "Re-validate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The model did not answer in time.");
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-validate" })).toBeEnabled());
  });

  it("reattaches on load: a re-validation already running shows every Re-validate busy until it finishes", async () => {
    // The operation is per tender and re-validates every document, so after a
    // reload every row shows busy, exactly as after a click.
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([doc("tender-a"), { ...doc("tender-a"), id: "doc-2" }]);
    listPricingFindings.mockResolvedValueOnce([]).mockResolvedValueOnce([typed("f1", {})]);
    getActiveOperation.mockResolvedValue(operation("running"));
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    renderSingle();

    const buttons = await screen.findAllByRole("button", { name: /^Re-validat/ });
    expect(getActiveOperation).toHaveBeenCalledWith("tender-a", "commercial_validation");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(buttons[0]).toHaveAttribute("aria-busy", "true"));
    expect(buttons[1]).toHaveAttribute("aria-busy", "true");
    expect(buttons[1]).toHaveTextContent("Re-validating...");
    expect(buttons[0]).toBeDisabled();
    expect(validatePricingDocument).not.toHaveBeenCalled();
    expect(mintOperationEventsTicket).toHaveBeenCalledWith("op-tender-a");

    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    pushStatus();
    await screen.findByText("note f1");
    await waitFor(() => expect(buttons[0]).toBeEnabled());
    expect(buttons[1]).not.toHaveAttribute("aria-busy");
  });
});

// Upload-list race, sibling screens (useListReads.ts): the re-read after a
// finished re-validation can be in flight when a Resolve lands. Its answer
// predates the resolve and must not reopen the finding, while the new
// finding it carries must still appear.
describe("a resolve that lands while the re-read after a re-validation is in flight", () => {
  it("stays resolved, and the re-validation's new finding still appears", async () => {
    const user = userEvent.setup();
    const open = { ...finding("tender-a", "note f1"), id: "f1" };
    const added = { ...finding("tender-a", "note f2"), id: "f2" };
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([doc("tender-a")]);
    let answerReread: (rows: PricingFindingResponse[]) => void = () => {};
    listPricingFindings.mockResolvedValueOnce([open]).mockReturnValueOnce(
      new Promise<PricingFindingResponse[]>((r) => {
        answerReread = r;
      }),
    );
    validatePricingDocument.mockResolvedValue(operation("queued"));
    getOperation.mockResolvedValue(operation("succeeded"));
    resolvePricingFinding.mockResolvedValue({ ...open, resolved: true });
    const { container } = render(
      <MemoryRouter initialEntries={["/tenders/tender-a/commercial"]}>
        <Routes>
          <Route path="/tenders/:tenderId/commercial" element={<CommercialFindings />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("note f1");
    await user.click(screen.getByRole("button", { name: /^Re-validat/ }));
    await waitFor(() => expect(listPricingFindings).toHaveBeenCalledTimes(2));
    const findingsTable = () => container.querySelectorAll("table")[1] as HTMLElement;
    await user.click(within(findingsTable()).getByRole("button", { name: "Resolve" }));
    await waitFor(() =>
      expect(within(findingsTable()).queryAllByRole("button", { name: "Resolve" })).toHaveLength(0),
    );

    answerReread([open, added]);

    expect(await screen.findByText("note f2")).toBeInTheDocument();
    // Only f2 is open: f1's resolve survived the older answer.
    expect(within(findingsTable()).getAllByRole("button", { name: "Resolve" })).toHaveLength(1);
  });
});
