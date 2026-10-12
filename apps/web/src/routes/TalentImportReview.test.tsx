import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { ImportBatchResponse, ImportItemDetailResponse } from "../api/client";
import { TalentImportReview } from "./TalentImportReview";

// Restyle batch 6. Pins the DOM that full-flow.spec.ts :310-427 reads: the
// résumé field's label and enabled state (:317, :329), the first <table>'s
// row cells by index (:344-353), "Review items" (:387), the Items section
// and its per-row Approve (:388-424) - plus the restyle's own promises.
// Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listImportBatches: vi.fn(),
    listImportItems: vi.fn(),
    approveImportItem: vi.fn(),
    rejectImportItem: vi.fn(),
    uploadResumes: vi.fn(),
    uploadEmployeeMaster: vi.fn(),
    uploadCertifications: vi.fn(),
  };
});

vi.mock("../auth/useAuth", () => ({
  useAuth: () => ({ organizationId: "org-1" }),
}));

const listImportBatches = vi.mocked(apiClient.listImportBatches);
const listImportItems = vi.mocked(apiClient.listImportItems);
const approveImportItem = vi.mocked(apiClient.approveImportItem);
const uploadResumes = vi.mocked(apiClient.uploadResumes);

function batch(overrides: Partial<ImportBatchResponse> = {}): ImportBatchResponse {
  return {
    id: "b1",
    organization_id: "org-1",
    import_type: "bulk_resumes",
    source_document_id: null,
    status: "needs_review",
    total_items: 3,
    completed_items: 2,
    failed_items: 1,
    created_at: "2026-10-10T08:00:00Z",
    ...overrides,
  };
}

function item(overrides: Partial<ImportItemDetailResponse> = {}): ImportItemDetailResponse {
  return {
    id: "i1",
    batch_id: "b1",
    source_ref: "resume_avery_quill.docx",
    status: "approved",
    target_knowledge_item_id: null,
    error_code: null,
    error_message: null,
    created_at: "2026-10-10T08:00:00Z",
    updated_at: "2026-10-10T08:00:00Z",
    extracted_payload: { name: "Avery Quill", email: "avery@example.test", confidence: 0.92 },
    ...overrides,
  };
}

const NEEDS_REVIEW = item({
  id: "i2",
  source_ref: "resume_rowan_quill.docx",
  status: "needs_review",
  error_message: "Possible duplicate of an existing employee; confirm the match.",
  extracted_payload: {
    name: "Rowan Quill",
    current_title: "Surveyor",
    years_experience: 1,
    certification_mentions: [],
    project_mentions: [],
    confidence: 0.61,
  },
});

const FAILED = item({
  id: "i3",
  source_ref: "_batch_parse_failure",
  status: "failed",
  error_message: "Could not read the uploaded file.",
  extracted_payload: {},
});

beforeEach(() => {
  listImportBatches.mockResolvedValue([batch()]);
  listImportItems.mockResolvedValue([item(), NEEDS_REVIEW, FAILED]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderScreen() {
  return render(
    <MemoryRouter>
      <TalentImportReview />
    </MemoryRouter>,
  );
}

async function openItems(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Review items" }));
  await screen.findByText("Rowan Quill");
}

function itemsSection(): HTMLElement {
  return screen.getByRole("heading", { name: "Items" }).closest("section")!;
}

describe("TalentImportReview (restyle batch 6)", () => {
  it("keeps the batch row's cells readable by index: digits only in total, completed, failed (full-flow.spec.ts:344-353)", async () => {
    const { container } = renderScreen();
    await screen.findByRole("button", { name: "Review items" });

    const firstTable = container.querySelectorAll("table")[0];
    const rows = firstTable.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(1);
    const cells = Array.from(rows[0].querySelectorAll("td"));
    expect(cells.map((c) => c.textContent)).toEqual([
      "Resumes",
      "Needs review",
      "3",
      "2",
      "1",
      "Review items",
    ]);
    // A row header (<th>) would not be a "td" and would shift every index.
    expect(rows[0].firstElementChild?.tagName).toBe("TD");
    // Polish pass: the actions cell is the narrow .cell-actions column; the
    // class changes no cell's text or index.
    expect(cells[5].classList.contains("cell-actions")).toBe(true);
  });

  it("keeps the batches table the first <table> with the items open", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();
    await openItems(user);
    const tables = container.querySelectorAll("table");
    expect(tables).toHaveLength(2);
    expect(tables[0].classList.contains("talent-import__batches")).toBe(true);
  });

  it("shows Done only when no item failed, else Done with failures", async () => {
    listImportBatches.mockResolvedValue([
      batch({ id: "b1", status: "completed", failed_items: 0, completed_items: 4, total_items: 4 }),
      batch({ id: "b2", status: "completed", failed_items: 1, completed_items: 3, total_items: 4 }),
    ]);
    const { container } = renderScreen();
    await screen.findByText("Done with failures");
    const statuses = Array.from(container.querySelectorAll("table tbody tr")).map(
      (r) => r.querySelectorAll("td")[1].textContent,
    );
    expect(statuses).toEqual(["Done", "Done with failures"]);
  });

  it("renders no placeholder rows while the batches load, and the empty state only after", async () => {
    let resolve!: (b: ImportBatchResponse[]) => void;
    listImportBatches.mockReturnValue(new Promise((r) => (resolve = r)));
    const { container } = renderScreen();

    const table = container.querySelector("table")!;
    expect(table.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(table.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByText("No import batches yet")).toBeNull();

    resolve([]);
    expect(await screen.findByText("No import batches yet")).toBeInTheDocument();
    expect(table.hasAttribute("aria-busy")).toBe(false);
    expect(table.querySelectorAll("tbody tr")).toHaveLength(0);
  });

  it("has exactly one section with an 'items' heading, and it is not nested in another section (full-flow.spec.ts:388-390)", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();
    await openItems(user);

    const sections = Array.from(container.querySelectorAll("section")).filter((s) =>
      Array.from(s.querySelectorAll("h1, h2, h3, h4, h5, h6")).some((h) =>
        /items/i.test(h.textContent ?? ""),
      ),
    );
    expect(sections).toHaveLength(1);
    expect(sections[0].parentElement?.closest("section")).toBeNull();
    // One row per item, no detail sub-rows (:402-405).
    expect(sections[0].querySelectorAll("tbody tr")).toHaveLength(3);
  });

  it("renders no placeholder rows while the items load (full-flow.spec.ts:396-401)", async () => {
    const user = userEvent.setup();
    let resolve!: (i: ImportItemDetailResponse[]) => void;
    listImportItems.mockReturnValue(new Promise((r) => (resolve = r)));
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));

    const table = within(itemsSection()).getByRole("table");
    expect(table.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(table.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByText("No items in this batch yet")).toBeNull();

    resolve([]);
    expect(await screen.findByText("No items in this batch yet")).toBeInTheDocument();
  });

  it("puts Approve and Reject in a needs-review row only, and Approve removes them (full-flow.spec.ts:408-424)", async () => {
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);

    const rows = within(itemsSection()).getAllByRole("row").slice(1);
    expect(
      rows.map((r) =>
        within(r)
          .queryAllByRole("button")
          .map((b) => b.textContent),
      ),
    ).toEqual([[], ["Approve", "Reject"], []]);

    approveImportItem.mockResolvedValue(NEEDS_REVIEW);
    listImportItems.mockResolvedValue([item(), { ...NEEDS_REVIEW, status: "approved" }, FAILED]);
    await user.click(within(rows[1]).getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(within(itemsSection()).queryByRole("button", { name: "Approve" })).toBeNull(),
    );
    expect(approveImportItem).toHaveBeenCalledWith("i2");
  });

  it("shows a needs-review item's reason AND its parsed fields, in words", async () => {
    const user = userEvent.setup();
    const { container } = renderScreen();
    await openItems(user);

    const row = within(itemsSection()).getAllByRole("row")[2];
    expect(row).toHaveTextContent("Possible duplicate of an existing employee; confirm the match.");
    expect(within(row).getByText("Rowan Quill")).toBeInTheDocument();
    expect(within(row).getByText("Surveyor")).toBeInTheDocument();
    expect(within(row).getByText("1 year")).toBeInTheDocument();
    expect(within(row).getByText("61%")).toBeInTheDocument();
    expect(within(row).getByText("Needs review")).toBeInTheDocument();
    // The failed sentinel: its reason, no fields, and a named source.
    expect(screen.getByText("Whole file")).toBeInTheDocument();
    // The résumé file name breaks after its underscores (the shared
    // BreakAfterUnderscores) with its text unchanged.
    const source = row.querySelector("td")!;
    expect(source.textContent).toBe("resume_rowan_quill.docx");
    expect(source.querySelectorAll("wbr")).toHaveLength(2);
    expect(screen.getByText("Could not read the uploaded file.")).toBeInTheDocument();

    // No JSON dump and no raw enum anywhere.
    const leaves = Array.from(container.querySelectorAll("main *")).filter(
      (el) => el.children.length === 0,
    );
    expect(leaves.some((el) => (el.textContent ?? "").startsWith("{"))).toBe(false);
    expect(container.textContent).not.toContain("[object Object]");
    for (const raw of ["bulk_resumes", "needs_review", "_batch_parse_failure"]) {
      expect(container.textContent).not.toContain(raw);
    }
  });

  // Polish pass (P8): the open batch's row is marked for assistive tech.
  it("marks the open batch row aria-current, and only that row", async () => {
    const user = userEvent.setup();
    listImportBatches.mockResolvedValue([batch(), batch({ id: "b2", import_type: "manual" })]);
    const { container } = renderScreen();
    await user.click((await screen.findAllByRole("button", { name: "Review items" }))[0]);
    await screen.findByText("Rowan Quill");
    const rows = container.querySelectorAll("table")[0].querySelectorAll("tbody tr");
    expect(rows[0].getAttribute("aria-current")).toBe("true");
    expect(rows[1].hasAttribute("aria-current")).toBe(false);
  });

  // Polish pass (item 18b): "Review items" on the batch already open fetches
  // its items again (before, re-selecting the same id did nothing).
  it("refetches the open batch's items when Review items is clicked again", async () => {
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);
    expect(listImportItems).toHaveBeenCalledTimes(1);

    listImportItems.mockResolvedValue([{ ...NEEDS_REVIEW, status: "approved" }]);
    await user.click(screen.getByRole("button", { name: "Review items" }));
    await waitFor(() => expect(listImportItems).toHaveBeenCalledTimes(2));
    expect(listImportItems).toHaveBeenLastCalledWith("b1");
    await waitFor(() =>
      expect(within(itemsSection()).queryByRole("button", { name: "Approve" })).toBeNull(),
    );
    expect(within(itemsSection()).getAllByRole("row")).toHaveLength(2);
  });

  it("names the selected batch above its items", async () => {
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);
    expect(
      within(itemsSection()).getByText(/^Resumes batch, created 10 Oct 2026/),
    ).toBeInTheDocument();
  });

  it("keeps the résumé label verbatim and disables its input while uploading (full-flow.spec.ts:317, :329)", async () => {
    const user = userEvent.setup();
    let resolve!: (b: ImportBatchResponse) => void;
    uploadResumes.mockReturnValue(new Promise((r) => (resolve = r)));
    renderScreen();

    const inputs = screen.getAllByLabelText(/Resumes \(multiple files\):/);
    expect(inputs).toHaveLength(1);
    const input = inputs[0] as HTMLInputElement;
    expect(input.type).toBe("file");
    expect(input.multiple).toBe(true);
    expect(screen.getByLabelText("Employee master (CSV or XLSX):")).toBeInTheDocument();
    expect(screen.getByLabelText("Certifications (XLSX):")).toBeInTheDocument();

    await user.upload(input, [new File(["x"], "resume_a.docx")]);
    expect(input).toBeDisabled();
    resolve(batch());
    await waitFor(() => expect(input).toBeEnabled());
  });

  it("shows a failed batch load as a notice", async () => {
    listImportBatches.mockRejectedValue(new Error("down"));
    const { container } = renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load import batches");
    expect(container.querySelector("table")!.hasAttribute("aria-busy")).toBe(false);
  });
});
