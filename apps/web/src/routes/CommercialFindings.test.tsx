import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { PricingDocumentResponse, PricingFindingResponse } from "../api/client";
import { CommercialFindings } from "./CommercialFindings";

// B17 Phase 1 - see KNOWN_LIMITATIONS.md, "[owner: B17] CommercialFindings.
// tsx's handleValidate hardcodes its own stale-write guard to never fire."
// This is the fix's own regression test: without it, tender A's re-fetched
// findings could render under tender B after a navigate-during-request race
// - exactly the "confirmed real... not yet reproduced live" gap that entry
// named as still open. Deterministic: validatePricingDocument's own promise
// is held open under test control, so the race is driven directly rather
// than hoped for via timing.

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
  };
});

const listPricingDocuments = vi.mocked(apiClient.listPricingDocuments);
const listPricingFindings = vi.mocked(apiClient.listPricingFindings);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const validatePricingDocument = vi.mocked(apiClient.validatePricingDocument);
const resolvePricingFinding = vi.mocked(apiClient.resolvePricingFinding);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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
    let resolveValidate: (() => void) | undefined;
    validatePricingDocument.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
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

    // Now let the stale, tender-A-scoped validate/refresh actually resolve.
    resolveValidate?.();
    // listPricingFindings is called at least twice already (both mounts);
    // waiting for a third call is what confirms handleValidate's own
    // refreshFindings actually ran to completion, not just that the promise
    // resolved - the guard's job is to run and then discard the result, not
    // to prevent the fetch.
    await vi.waitFor(() => expect(listPricingFindings).toHaveBeenCalledTimes(3));

    // The guard should have discarded that stale result - tender B's
    // finding must still be what's on screen.
    expect(screen.getByText("tender B finding")).toBeInTheDocument();
    expect(screen.queryByText("tender A finding")).not.toBeInTheDocument();
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
        new Promise<void>((_resolve, reject) => {
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

  it("marks only the clicked Re-validate aria-busy and disables them all while it runs", async () => {
    const user = userEvent.setup();
    listWorkflowRuns.mockResolvedValue([]);
    listPricingDocuments.mockResolvedValue([doc("tender-a"), { ...doc("tender-a"), id: "doc-2" }]);
    listPricingFindings.mockResolvedValue([]);
    let resolveValidate!: () => void;
    validatePricingDocument.mockReturnValue(new Promise<void>((r) => (resolveValidate = r)));
    renderSingle();

    const buttons = await screen.findAllByRole("button", { name: "Re-validate" });
    await user.click(buttons[0]);
    expect(buttons[0]).toHaveAttribute("aria-busy", "true");
    expect(buttons[1]).not.toHaveAttribute("aria-busy");
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1]).toBeDisabled();
    resolveValidate();
    await waitFor(() => expect(buttons[0]).toBeEnabled());
    expect(buttons[0]).not.toHaveAttribute("aria-busy");
  });
});
