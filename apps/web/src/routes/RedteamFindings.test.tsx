import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { OperationResponse, RedteamFindingResponse } from "../api/client";
import { RedteamFindings } from "./RedteamFindings";

// Restyle batch 5. Pins the cell contract full-flow.spec.ts reads on
// /redteam (:1373-1416, :1083-1101). Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listRedteamFindings: vi.fn(),
    resolveRedteamFinding: vi.fn(),
    triggerRedTeam: vi.fn(),
    getOperation: vi.fn(),
    mintOperationEventsTicket: vi.fn(),
    getActiveOperation: vi.fn(),
  };
});

const listRedteamFindings = vi.mocked(apiClient.listRedteamFindings);
const resolveRedteamFinding = vi.mocked(apiClient.resolveRedteamFinding);
const triggerRedTeam = vi.mocked(apiClient.triggerRedTeam);
const getOperation = vi.mocked(apiClient.getOperation);
const mintOperationEventsTicket = vi.mocked(apiClient.mintOperationEventsTicket);
const getActiveOperation = vi.mocked(apiClient.getActiveOperation);

// Phase 4: Run red team follows an async operation over SSE. jsdom has
// no EventSource, so a minimal fake records the streams opened.
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
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function operation(status: OperationResponse["status"]): OperationResponse {
  return {
    operation_id: "op-1",
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

function finding(
  id: string,
  overrides: Partial<RedteamFindingResponse> = {},
): RedteamFindingResponse {
  return {
    id,
    tender_id: "t1",
    target_type: "proposal_claim",
    target_id: null,
    finding_category: "deselected_fact_in_use",
    severity: "critical",
    finding_text: `Finding ${id}.`,
    rebuttal: null,
    evidence_refs: [],
    violated_condition: "c",
    resolved: false,
    resolution_note: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/redteam"]}>
      <Routes>
        <Route path="/tenders/:tenderId/redteam" element={<RedteamFindings />} />
      </Routes>
    </MemoryRouter>,
  );
}

function cells(row: Element) {
  return Array.from(row.querySelectorAll("td")).map((td) => (td.textContent ?? "").trim());
}

describe("RedteamFindings (restyle batch 5)", () => {
  it("keeps the spec's cells: raw category first, raw severity second, plain 'no'/'yes' fourth (full-flow.spec.ts:1380, :1382, :1409-1416)", async () => {
    listRedteamFindings.mockResolvedValue([
      finding("f1"),
      finding("f2", { severity: "major", finding_category: "pricing_mismatch" }),
      finding("f3", { severity: "minor", resolved: true }),
    ]);
    const { container } = renderScreen();

    await screen.findByText("Finding f3.");
    const rows = container.querySelector("table")!.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(3);
    expect(cells(rows[0]).slice(0, 4)).toEqual([
      "deselected_fact_in_use",
      "critical",
      "Finding f1.",
      "no",
    ]);
    expect(cells(rows[1])[1]).toBe("major");
    expect(cells(rows[2])[1]).toBe("minor");
    expect(cells(rows[2])[3]).toBe("yes");
    // The severity word is drawn capitalised by CSS, not rewritten.
    expect(rows[0].querySelector("td:nth-child(2) .badge-raw-word .status-badge")).not.toBeNull();
    // The spec's anchored category match still finds the D6 rows.
    const d6 = Array.from(rows).filter((r) =>
      /^deselected_fact_in_use$/.test(r.querySelector("td")!.textContent ?? ""),
    );
    expect(d6).toHaveLength(2);
    // Line-break hints after underscores add no text.
    expect(rows[0].querySelector("td")!.querySelectorAll("wbr")).toHaveLength(3);
    expect(rows[0].querySelector("td")!.textContent).toBe("deselected_fact_in_use");
    // Polish pass: only the actions column (td index 4, which the spec does
    // not read) is the narrow .cell-actions column; td 0-3 keep their place.
    const tds = rows[0].querySelectorAll("td");
    expect(tds).toHaveLength(5);
    expect(tds[4].classList.contains("cell-actions")).toBe(true);
    expect(Array.from(tds).filter((td) => td.classList.contains("cell-actions"))).toHaveLength(1);
    expect(container.querySelector("thead th:last-child")!.classList.contains("cell-actions")).toBe(
      true,
    );
  });

  it("offers Resolve only on open rows and removes it once resolved, with no other 'Resolve…' button on the page (full-flow.spec.ts:1083-1101)", async () => {
    const user = userEvent.setup();
    listRedteamFindings.mockResolvedValue([finding("f1"), finding("f2", { resolved: true })]);
    resolveRedteamFinding.mockResolvedValue(finding("f1", { resolved: true }));
    renderScreen();

    await screen.findByText("Finding f2.");
    // Page-wide, as the spec counts them; substring match like getByRole's default.
    const resolveButtons = () => screen.queryAllByRole("button", { name: /Resolve/ });
    expect(resolveButtons()).toHaveLength(1);
    await user.click(resolveButtons()[0]);
    await waitFor(() => expect(resolveButtons()).toHaveLength(0));
    expect(resolveRedteamFinding).toHaveBeenCalledWith("f1", null);
  });

  it("labels the resolution note field and sends what was typed", async () => {
    const user = userEvent.setup();
    listRedteamFindings.mockResolvedValue([finding("f1")]);
    resolveRedteamFinding.mockResolvedValue(finding("f1", { resolved: true }));
    renderScreen();

    const note = await screen.findByRole("textbox", { name: "Resolution note" });
    await user.type(note, "Accepted risk");
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    expect(resolveRedteamFinding).toHaveBeenCalledWith("f1", "Accepted risk");
  });

  it("is the page's first table and puts the empty state after it, outside it, once loaded", async () => {
    let resolve!: (rows: RedteamFindingResponse[]) => void;
    listRedteamFindings.mockReturnValue(new Promise((r) => (resolve = r)));
    const { container } = renderScreen();

    const table = container.querySelector("table")!;
    expect(table).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByText("No red-team findings yet")).toBeNull();
    resolve([]);
    const empty = await screen.findByText("No red-team findings yet");
    expect(empty.closest("table")).toBeNull();
    expect(table.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(table.querySelectorAll("tbody tr")).toHaveLength(0);
  });

  it("counts findings and open ones", async () => {
    listRedteamFindings.mockResolvedValue([finding("f1"), finding("f2", { resolved: true })]);
    renderScreen();
    expect(await screen.findByText("2 findings, 1 open")).toBeInTheDocument();
    const row = screen.getByText("Finding f2.").closest("tr")!;
    expect(row).toHaveClass("redteam__row--resolved");
    expect(within(row).queryByRole("button")).toBeNull();
  });

  // Polish pass (P3): a resolved row shows its resolution note in the
  // actions cell (td 4); the spec's cells (td 0, 1, 3) and the page-wide
  // "Resolve" count are unchanged. Fake note text.
  it("shows a resolved row's note in td 4 without changing the spec's cells or Resolve count", async () => {
    const LONG =
      "Checked with the bid manager: the claim now cites the close-out report and the client reference letter, so nothing further is needed before submission.";
    listRedteamFindings.mockResolvedValue([
      finding("f1"),
      finding("f2", { severity: "major", resolved: true, resolution_note: LONG }),
      finding("f3", { severity: "minor", resolved: true, resolution_note: null }),
    ]);
    const { container } = renderScreen();
    await screen.findByText(LONG);

    const rows = container.querySelectorAll("tbody tr");
    expect(cells(rows[1])).toEqual(["deselected_fact_in_use", "major", "Finding f2.", "yes", LONG]);
    expect(rows[1].querySelectorAll("td")[4].querySelector(".redteam__note")).not.toBeNull();
    // A resolved row with no note shows nothing there.
    expect(cells(rows[2])[4]).toBe("");
    // Still exactly one Resolve button (the open row), page-wide.
    expect(screen.getAllByRole("button", { name: /resolve/i })).toHaveLength(1);
  });

  // Phase 4: the POST only starts the run (202), so the button stays busy
  // until the operation reaches a finished status (full-flow.spec.ts
  // :1030-1036 waits for it to be enabled again), and then the findings are
  // re-read.
  it("keeps Run red team busy until the operation finishes, then re-reads the findings", async () => {
    const user = userEvent.setup();
    listRedteamFindings.mockResolvedValueOnce([]).mockResolvedValueOnce([finding("f1")]);
    triggerRedTeam.mockResolvedValue(operation("queued"));
    mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    renderScreen();

    await screen.findByText("No red-team findings yet");
    await user.click(screen.getByRole("button", { name: "Run red team" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    // Still running: the button stays busy and the findings are not re-read.
    expect(screen.getByRole("button", { name: /Running/ })).toBeDisabled();
    expect(listRedteamFindings).toHaveBeenCalledTimes(1);

    act(() => FakeEventSource.instances[0]!.onmessage?.(new MessageEvent("message")));
    await screen.findByText("Finding f1.");
    expect(screen.getByRole("button", { name: "Run red team" })).toBeEnabled();
    expect(listRedteamFindings).toHaveBeenCalledTimes(2);
  });

  it("shows a failed operation's own message and frees the button", async () => {
    const user = userEvent.setup();
    listRedteamFindings.mockResolvedValue([]);
    triggerRedTeam.mockResolvedValue(operation("queued"));
    mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
    getOperation.mockResolvedValue(operation("failed"));
    renderScreen();

    await screen.findByText("No red-team findings yet");
    await user.click(screen.getByRole("button", { name: "Run red team" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The model did not answer in time.");
    expect(screen.getByRole("button", { name: "Run red team" })).toBeEnabled();
  });

  // Phase 4 Stage 2: a run started before a reload is picked up again on
  // load, exactly as after a click.
  it("reattaches on load: a red-team run already running shows the button busy, then re-reads the findings", async () => {
    listRedteamFindings.mockResolvedValueOnce([]).mockResolvedValueOnce([finding("f1")]);
    getActiveOperation.mockResolvedValue(operation("running"));
    mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    renderScreen();

    expect(await screen.findByRole("button", { name: /Running/ })).toBeDisabled();
    expect(getActiveOperation).toHaveBeenCalledWith("t1", "redteam_run");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(mintOperationEventsTicket).toHaveBeenCalledWith("op-1");
    expect(triggerRedTeam).not.toHaveBeenCalled();

    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    act(() => FakeEventSource.instances[0]!.onmessage?.(new MessageEvent("message")));
    await screen.findByText("Finding f1.");
    expect(screen.getByRole("button", { name: "Run red team" })).toBeEnabled();
  });
});

// Stale-tender guard (Stage 2 review): the re-read after a finished run can
// answer after the user has moved to another tender. The component stays
// mounted across the tenderId change, as in App.tsx's own routing.
describe("RedteamFindings (stale-tender guard)", () => {
  it("a finished run's re-read of tender A, still in flight after navigating away, does not land on tender B", async () => {
    const user = userEvent.setup();
    let resolveStaleRead!: (rows: RedteamFindingResponse[]) => void;
    let tenderAReads = 0;
    listRedteamFindings.mockImplementation((tenderId) => {
      if (tenderId === "t2") return Promise.resolve([finding("b1", { tender_id: "t2" })]);
      tenderAReads += 1;
      if (tenderAReads === 1) return Promise.resolve([]);
      return new Promise((r) => (resolveStaleRead = r));
    });
    triggerRedTeam.mockResolvedValue(operation("queued"));
    mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    render(
      <MemoryRouter initialEntries={["/tenders/t1/redteam"]}>
        <Link to="/tenders/t2/redteam">switch to t2</Link>
        <Routes>
          <Route path="/tenders/:tenderId/redteam" element={<RedteamFindings />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("No red-team findings yet");
    await user.click(screen.getByRole("button", { name: "Run red team" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    act(() => FakeEventSource.instances[0]!.onmessage?.(new MessageEvent("message")));
    // The re-read of tender A has started and is held open.
    await waitFor(() => expect(tenderAReads).toBe(2));

    await user.click(screen.getByText("switch to t2"));
    expect(await screen.findByText("Finding b1.")).toBeInTheDocument();

    resolveStaleRead([finding("a-stale")]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByText("Finding b1.")).toBeInTheDocument();
    expect(screen.queryByText("Finding a-stale.")).toBeNull();
  });
});
