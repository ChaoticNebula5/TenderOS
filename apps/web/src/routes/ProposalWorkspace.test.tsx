import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
  OperationResponse,
  ProposalClaimResponse,
  ProposalClaimTraceResponse,
  ProposalSectionResponse,
} from "../api/client";
import { ProposalWorkspace } from "./ProposalWorkspace";

// Restyle batch 5. Pins the DOM full-flow.spec.ts reads on /proposal:
// sections as direct children of <main> (:952), claim <li>s holding
// "/ unsupported]" (:971, :974) with their "Inspect trace" button (:972),
// and the drawer's focus return. Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listProposalSections: vi.fn(),
    listProposalClaims: vi.fn(),
    generateProposal: vi.fn(),
    patchProposalSection: vi.fn(),
    traceProposalClaim: vi.fn(),
    getActiveOperation: vi.fn(),
    getOperation: vi.fn(),
    mintOperationEventsTicket: vi.fn(),
  };
});

const listProposalSections = vi.mocked(apiClient.listProposalSections);
const listProposalClaims = vi.mocked(apiClient.listProposalClaims);
const traceProposalClaim = vi.mocked(apiClient.traceProposalClaim);
const generateProposal = vi.mocked(apiClient.generateProposal);
const getActiveOperation = vi.mocked(apiClient.getActiveOperation);
const getOperation = vi.mocked(apiClient.getOperation);
const mintOperationEventsTicket = vi.mocked(apiClient.mintOperationEventsTicket);

// Phase 4 Stage 2: Generate follows an async operation over SSE. jsdom has
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
  mintOperationEventsTicket.mockResolvedValue({ ticket: "fake-ticket", expires_in_seconds: 30 });
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
    kind: "proposal_generation",
    status,
    error_code: status === "failed" ? "LLM_TIMEOUT" : null,
    error_message: status === "failed" ? "The model did not answer in time." : null,
    created_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

function pushStatus() {
  act(() => FakeEventSource.instances.at(-1)!.onmessage?.(new MessageEvent("message")));
}

function section(id: string, status = "draft"): ProposalSectionResponse {
  return {
    id,
    tender_id: "t1",
    section_name: `Section ${id}`,
    content: "Drafted text.",
    version: 2,
    status,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function claim(id: string, grounding: string, type = "capability"): ProposalClaimResponse {
  return {
    id,
    section_id: "s1",
    claim_text: `Claim ${id}.`,
    claim_type: type,
    is_factual: true,
    grounding_status: grounding,
  };
}

function trace(): ProposalClaimTraceResponse {
  return {
    claim_id: "c1",
    claim_text: "Claim c1.",
    claim_type: "capability",
    is_factual: true,
    grounding_status: "unsupported",
    section_id: "s1",
    section_name: "Section s1",
    requirement_ids: [],
    employee_knowledge_item_ids: [],
    evidence: [],
    currently_in_scope_evidence: [],
    section_is_data_gated: true,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/proposal"]}>
      <Routes>
        <Route path="/tenders/:tenderId/proposal" element={<ProposalWorkspace />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProposalWorkspace (restyle batch 5)", () => {
  it("renders every section as a direct child of <main> (full-flow.spec.ts:952)", async () => {
    listProposalSections.mockResolvedValue([section("s1"), section("s2", "approved")]);
    listProposalClaims.mockResolvedValue([]);
    const { container } = renderScreen();

    await screen.findByRole("heading", { name: "Section s2" });
    expect(container.querySelectorAll("main > section")).toHaveLength(2);
    expect(screen.getAllByText("Version 2")).toHaveLength(2);
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
  });

  it("keeps the empty state out of `main > section` and shows it only after the response", async () => {
    listProposalSections.mockResolvedValue([]);
    const { container } = renderScreen();

    expect(await screen.findByText("No proposal sections drafted yet")).toBeInTheDocument();
    expect(container.querySelectorAll("main > section")).toHaveLength(0);
  });

  it("writes the claim tag so an unsupported claim's li holds exactly '/ unsupported]' (full-flow.spec.ts:971, :974)", async () => {
    listProposalSections.mockResolvedValue([section("s1")]);
    listProposalClaims.mockResolvedValue([
      claim("c1", "unsupported"),
      claim("c2", "grounded", "team"),
      claim("c3", "needs_review"),
      claim("c4", "not_required", "other"),
    ]);
    const { container } = renderScreen();

    await screen.findByText("Claim c4.");
    const items = Array.from(container.querySelectorAll("li.claim"));
    const text = (i: number) => items[i].textContent ?? "";
    // Humanised type; raw grounding word, bracket straight after it.
    expect(text(0).startsWith("[Capability / unsupported]")).toBe(true);
    expect(text(0)).toContain("/ unsupported]");
    expect(text(1).startsWith("[Team / grounded]")).toBe(true);
    // Multi-word values show their mapped labels, not raw words.
    expect(text(2).startsWith("[Capability / Needs review]")).toBe(true);
    expect(text(3).startsWith("[Other / Not required]")).toBe(true);
    // Only the unsupported claim matches the spec's text filter.
    expect(items.filter((li) => li.textContent?.includes("/ unsupported]"))).toHaveLength(1);
    // CSS capitalises only the raw-word badges.
    expect(items[0].querySelector(".badge-raw-word .status-badge")).not.toBeNull();
    expect(items[2].querySelector(".badge-raw-word")).toBeNull();
    // Each claim li holds its own Inspect trace button (:972).
    expect(
      within(items[0] as HTMLElement).getByRole("button", { name: "Inspect trace" }),
    ).toBeInTheDocument();
    // Polish pass (P4): the button is a sibling of the tag-and-text block,
    // still inside the li, so it can sit on its own line.
    const button = within(items[0] as HTMLElement).getByRole("button", { name: "Inspect trace" });
    expect(button.parentElement).toBe(items[0]);
    expect(items[0].querySelector(".claim__body")!.textContent).toBe(
      "[Capability / unsupported] Claim c1.",
    );
  });

  // Polish pass (P4). The spec never reads "Save edits" (0 matches).
  it("shows Save edits only while a section has an unsaved change, and hides it after saving", async () => {
    const user = userEvent.setup();
    listProposalSections.mockResolvedValue([section("s1"), section("s2")]);
    listProposalClaims.mockResolvedValue([]);
    vi.mocked(apiClient.patchProposalSection).mockResolvedValue({
      ...section("s1"),
      content: "Drafted text. Edited.",
      version: 3,
    });
    renderScreen();

    await screen.findByRole("heading", { name: "Section s2" });
    expect(screen.queryByRole("button", { name: "Save edits" })).toBeNull();

    const [field] = screen.getAllByLabelText("Section text");
    await user.type(field, " Edited.");
    expect(screen.getAllByRole("button", { name: "Save edits" })).toHaveLength(1);

    // Back to the stored text: nothing to save, no button.
    await user.clear(field);
    await user.type(field, "Drafted text.");
    expect(screen.queryByRole("button", { name: "Save edits" })).toBeNull();

    await user.type(field, " Edited.");
    await user.click(screen.getByRole("button", { name: "Save edits" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save edits" })).toBeNull());
  });

  it("returns focus to the exact Inspect trace button that opened the drawer, which still exists after close", async () => {
    const user = userEvent.setup();
    listProposalSections.mockResolvedValue([section("s1")]);
    listProposalClaims.mockResolvedValue([claim("c1", "unsupported"), claim("c2", "grounded")]);
    traceProposalClaim.mockResolvedValue(trace());
    renderScreen();

    await screen.findByText("Claim c2.");
    const triggers = screen.getAllByRole("button", { name: "Inspect trace" });
    await user.click(triggers[1]);
    await screen.findByRole("heading", { name: "Claim trace" });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(triggers[1].isConnected).toBe(true);
    expect(document.activeElement).toBe(triggers[1]);
  });

  // Batch 5 fix 2: the none-row only after that section's claims answered.
  it("shows 'No claims in this section.' only once that section's claims have loaded empty", async () => {
    listProposalSections.mockResolvedValue([section("s1")]);
    let resolve!: (claims: ProposalClaimResponse[]) => void;
    listProposalClaims.mockReturnValue(new Promise((r) => (resolve = r)));
    const { container } = renderScreen();

    await screen.findByRole("heading", { name: "Section s1" });
    expect(screen.queryByText("No claims in this section.")).toBeNull();
    expect(container.querySelectorAll("ul.claims li")).toHaveLength(0);
    resolve([]);
    expect(await screen.findByText("No claims in this section.")).toBeInTheDocument();
  });
});

// Phase 4 Stage 2: Generate proposal starts an async operation (202) and the
// worker drafts. full-flow.spec.ts:949-953 waits up to 300 s for the button
// to be enabled, then expects a section visible at once, and step 10 (:974)
// counts claim rows without waiting: so the button must stay disabled until
// the operation is finished AND the sections and their claims are re-read.
describe("ProposalWorkspace (Phase 4 Stage 2, async generation)", () => {
  it("keeps Generate busy until the operation finishes, and re-reads sections and claims before releasing it", async () => {
    const user = userEvent.setup();
    let resolveSections!: (sections: ProposalSectionResponse[]) => void;
    listProposalSections
      .mockResolvedValueOnce([])
      .mockReturnValueOnce(new Promise((r) => (resolveSections = r)));
    let resolveClaims!: (claims: ProposalClaimResponse[]) => void;
    listProposalClaims.mockReturnValue(new Promise((r) => (resolveClaims = r)));
    generateProposal.mockResolvedValue(operation("queued"));
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    const { container } = renderScreen();

    await screen.findByText("No proposal sections drafted yet");
    await user.click(screen.getByRole("button", { name: "Generate proposal" }));
    expect(generateProposal).toHaveBeenCalledWith("t1", expect.any(String));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    // Accepted and running: busy, and no "Generate proposal" button to find.
    expect(screen.getByRole("button", { name: "Generating..." })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Generate proposal" })).toBeNull();
    expect(listProposalSections).toHaveBeenCalledTimes(1);

    // Finished: the re-read starts, the button is still busy.
    pushStatus();
    await waitFor(() => expect(listProposalSections).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Generating..." })).toBeDisabled();

    // Sections answered, claims not yet: still busy.
    resolveSections([section("s1")]);
    await screen.findByRole("heading", { name: "Section s1" });
    expect(screen.getByRole("button", { name: "Generating..." })).toBeDisabled();

    // Claims answered: only now is the button released, with the section and
    // its claim rows already on screen.
    resolveClaims([claim("c1", "unsupported")]);
    const released = await screen.findByRole("button", { name: "Generate proposal" });
    expect(released).toBeEnabled();
    expect(container.querySelectorAll("main > section")).toHaveLength(1);
    expect(screen.getByText("Claim c1.").closest("li")!.textContent).toContain("/ unsupported]");
  });

  it("shows a failed generation's own message and frees the button", async () => {
    const user = userEvent.setup();
    listProposalSections.mockResolvedValue([]);
    generateProposal.mockResolvedValue(operation("queued"));
    getOperation.mockResolvedValue(operation("failed"));
    renderScreen();

    await screen.findByText("No proposal sections drafted yet");
    await user.click(screen.getByRole("button", { name: "Generate proposal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The model did not answer in time.");
    expect(await screen.findByRole("button", { name: "Generate proposal" })).toBeEnabled();
  });

  it("reattaches on load: a generation already running shows the button busy and follows it to the end", async () => {
    listProposalSections.mockResolvedValueOnce([]).mockResolvedValueOnce([section("s1")]);
    listProposalClaims.mockResolvedValue([]);
    getActiveOperation.mockResolvedValue(operation("running"));
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    renderScreen();

    expect(await screen.findByRole("button", { name: "Generating..." })).toBeDisabled();
    expect(getActiveOperation).toHaveBeenCalledWith("t1", "proposal_generation");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(mintOperationEventsTicket).toHaveBeenCalledWith("op-1");
    expect(generateProposal).not.toHaveBeenCalled();

    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    pushStatus();
    await screen.findByRole("heading", { name: "Section s1" });
    expect(await screen.findByRole("button", { name: "Generate proposal" })).toBeEnabled();
  });

  it("does not reattach when nothing is running", async () => {
    listProposalSections.mockResolvedValue([]);
    renderScreen();

    await screen.findByText("No proposal sections drafted yet");
    await waitFor(() => expect(getActiveOperation).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Generate proposal" })).toBeEnabled();
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

// Stale-tender guard (Stage 2 review): the re-read after a finished
// generation can answer after the user has moved to another tender. The
// component stays mounted across the tenderId change, as in App.tsx's own
// routing, so the late answer would otherwise land on tender B's screen.
describe("ProposalWorkspace (stale-tender guard)", () => {
  it("a finished generation's re-read of tender A, still in flight after navigating away, does not land on tender B", async () => {
    const user = userEvent.setup();
    let resolveStaleRead!: (sections: ProposalSectionResponse[]) => void;
    let tenderAReads = 0;
    listProposalSections.mockImplementation((tenderId) => {
      if (tenderId === "t2") return Promise.resolve([section("b1")]);
      tenderAReads += 1;
      if (tenderAReads === 1) return Promise.resolve([]);
      return new Promise((r) => (resolveStaleRead = r));
    });
    listProposalClaims.mockResolvedValue([]);
    generateProposal.mockResolvedValue(operation("queued"));
    getOperation
      .mockResolvedValueOnce(operation("running"))
      .mockResolvedValueOnce(operation("succeeded"));
    render(
      <MemoryRouter initialEntries={["/tenders/t1/proposal"]}>
        <Link to="/tenders/t2/proposal">switch to t2</Link>
        <Routes>
          <Route path="/tenders/:tenderId/proposal" element={<ProposalWorkspace />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("No proposal sections drafted yet");
    await user.click(screen.getByRole("button", { name: "Generate proposal" }));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(getOperation).toHaveBeenCalledTimes(1));
    pushStatus();
    // The re-read of tender A has started and is held open.
    await waitFor(() => expect(tenderAReads).toBe(2));

    await user.click(screen.getByText("switch to t2"));
    expect(await screen.findByRole("heading", { name: "Section b1" })).toBeInTheDocument();

    resolveStaleRead([section("a-stale")]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("heading", { name: "Section b1" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Section a-stale" })).toBeNull();
  });
});
