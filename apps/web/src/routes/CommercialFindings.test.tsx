import { cleanup, render, screen } from "@testing-library/react";
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
  };
});

const listPricingDocuments = vi.mocked(apiClient.listPricingDocuments);
const listPricingFindings = vi.mocked(apiClient.listPricingFindings);
const listWorkflowRuns = vi.mocked(apiClient.listWorkflowRuns);
const listGates = vi.mocked(apiClient.listGates);
const validatePricingDocument = vi.mocked(apiClient.validatePricingDocument);

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
});
