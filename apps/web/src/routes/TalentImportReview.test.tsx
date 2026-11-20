import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    importItemAsNewEmployee: vi.fn(),
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
const importItemAsNewEmployee = vi.mocked(apiClient.importItemAsNewEmployee);
const uploadResumes = vi.mocked(apiClient.uploadResumes);
const rejectImportItem = vi.mocked(apiClient.rejectImportItem);

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
    target_employee: null,
    // Plan item 111: the API's candidates field, null unless a test sets it.
    candidates: null,
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
  // Plan item 80: a resume item in review offers Approve only when it names
  // an employee, so the reachable Approve case carries one.
  target_knowledge_item_id: "e1",
  target_employee: {
    knowledge_item_id: "e1",
    // The strong-signal case whose name differs: the employee keeps theirs.
    name: "Rowan A. Quill",
    email: null,
    current_title: "Junior Surveyor",
    professional_location: "Pune",
    years_experience: null,
    resume_document_id: "d1",
    profile_review_status: "verified",
    fingerprint: "fp-1",
    merged_into_existing: false,
    // Plan item 80: the API's own change list (resume_merge_fields), which
    // the panel renders as is.
    changes: [
      { field: "email", on_file: null, from_resume: null, changes: false },
      {
        field: "current_title",
        on_file: "Junior Surveyor",
        from_resume: "Surveyor",
        changes: true,
      },
      { field: "professional_location", on_file: "Pune", from_resume: null, changes: false },
      { field: "years_experience", on_file: null, from_resume: 1, changes: true },
    ],
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
    expect(approveImportItem).toHaveBeenCalledWith("i2", "fp-1");
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

describe("plan item 78: batches update without a reload", () => {
  function batchRow(): HTMLElement {
    return document.querySelector("table tbody tr") as HTMLElement;
  }

  it("asks for no reload any more", async () => {
    renderScreen();
    await screen.findByText("Needs review");
    expect(screen.queryByText(/reload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/refresh/i)).not.toBeInTheDocument();
  });

  it("reads the batch list again after a Reject, so the batch row follows its items", async () => {
    const user = userEvent.setup();
    listImportBatches
      .mockResolvedValueOnce([batch()])
      .mockResolvedValue([batch({ status: "completed" })]);
    listImportItems
      .mockResolvedValueOnce([item(), NEEDS_REVIEW, FAILED])
      .mockResolvedValue([item(), { ...NEEDS_REVIEW, status: "rejected" }, FAILED]);
    rejectImportItem.mockResolvedValue({ ...NEEDS_REVIEW, status: "rejected" });
    renderScreen();
    await screen.findByText("Needs review");
    await user.click(screen.getByRole("button", { name: "Review items" }));
    await user.click(await screen.findByRole("button", { name: "Reject" }));

    await waitFor(() => expect(within(batchRow()).getByText("Done with failures")).toBeTruthy());
    expect(listImportBatches).toHaveBeenCalledTimes(2);
  });

  describe("with fake timers", () => {
    async function advance(ms: number) {
      if (ms === 0) {
        await act(async () => {});
        return;
      }
      for (let left = ms; left > 0; left -= 1_000) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(Math.min(left, 1_000));
        });
      }
    }

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("follows a batch from queued to done, with the open batch's items, and then stops reading", async () => {
      listImportBatches
        .mockResolvedValueOnce([
          batch({ status: "queued", total_items: 0, completed_items: 0, failed_items: 0 }),
        ])
        .mockResolvedValueOnce([
          batch({ status: "processing", total_items: 1, completed_items: 0, failed_items: 0 }),
        ])
        .mockResolvedValue([
          batch({ status: "completed", total_items: 1, completed_items: 1, failed_items: 0 }),
        ]);
      listImportItems
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([item({ status: "pending" })])
        .mockResolvedValue([item()]);

      renderScreen();
      await advance(0);
      expect(within(batchRow()).getByText("Queued")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Review items" }));
      await advance(0);

      await advance(2_000);
      expect(within(batchRow()).getByText("Processing")).toBeTruthy();
      await advance(2_000);
      expect(within(batchRow()).getByText("Done")).toBeTruthy();
      expect(
        within(screen.getByRole("region", { name: "Import items" })).getAllByText("Approved"),
      ).toHaveLength(1);

      const batchReads = listImportBatches.mock.calls.length;
      const itemReads = listImportItems.mock.calls.length;
      await advance(10 * 60_000);
      expect(listImportBatches).toHaveBeenCalledTimes(batchReads);
      expect(listImportItems).toHaveBeenCalledTimes(itemReads);
    });

    it("does not bring back Approve from a read sent before the Approve", async () => {
      const processing = batch({ status: "processing", total_items: 2, completed_items: 1 });
      listImportBatches.mockResolvedValue([processing]);
      listImportItems.mockResolvedValue([item(), NEEDS_REVIEW]);

      renderScreen();
      await advance(0);
      fireEvent.click(screen.getByRole("button", { name: "Review items" }));
      await advance(0);
      expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();

      // The poll's read of the items goes out, and is slow.
      let answerSlowRead: (items: ImportItemDetailResponse[]) => void = () => {};
      listImportItems.mockImplementationOnce(
        () => new Promise((resolve) => (answerSlowRead = resolve)),
      );
      await advance(2_000);

      // Approve, and the read after it, both answer first.
      approveImportItem.mockResolvedValue({ ...NEEDS_REVIEW, status: "approved" });
      listImportItems.mockResolvedValue([item(), { ...NEEDS_REVIEW, status: "approved" }]);
      fireEvent.click(screen.getByRole("button", { name: "Approve" }));
      await advance(0);
      expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();

      // The slow read answers with the item still needing review: dropped.
      await act(async () => answerSlowRead([item(), NEEDS_REVIEW]));
      expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    });
  });
});

// Plan item 80 (PR 18): a resume item in review names the employee it would
// merge into and what approving changes; one naming no one offers Reject
// only; a stale fingerprint (409 IMPORT_ITEM_TARGET_CHANGED) reloads the row.
describe("plan item 80: approving a resume merges it", () => {
  const NO_TARGET = item({
    id: "i4",
    source_ref: "0ae13cd8-1525-4c67-af84-83532c24d8f0/CV.docx",
    status: "needs_review",
    error_message: "conflicting strong signals matched different employees: email",
    extracted_payload: { name: "Sasha Quill", confidence: 0.7 },
  });

  it("shows whom the resume matches and each field it changes or keeps", async () => {
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);

    const row = within(itemsSection()).getAllByRole("row")[2];
    expect(within(row).getByRole("link", { name: "Rowan A. Quill" })).toHaveAttribute(
      "href",
      "/talent/employees/e1",
    );
    expect(row).toHaveTextContent("verified profile");
    const changes = within(row).getByLabelText("Changes on approval");
    expect(changes).toHaveTextContent("Junior Surveyor → Surveyor (changes)");
    expect(changes).toHaveTextContent("Pune (kept)");
    expect(changes).toHaveTextContent("Not set → 1 year (changes)");
    expect(changes).toHaveTextContent("Not set (kept)");
  });

  // Plan item 111: a resume naming no one now has its candidates (the API's
  // candidates field); one whose fields or document cannot be read comes
  // with none, and that is the item that can only be rejected.
  it("offers only Reject for a resume naming no one whose candidates cannot be read", async () => {
    listImportItems.mockResolvedValue([item(), NO_TARGET]);
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));
    await screen.findByText("Sasha Quill");

    const row = within(itemsSection()).getAllByRole("row")[2];
    expect(
      within(row)
        .queryAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Reject"]);
    expect(row).toHaveTextContent("could not be read, so it can only be rejected");
    // The file name, not the document id it is stored with.
    expect(row.querySelector("td")!.textContent).toBe("CV.docx");
  });

  it("keeps Approve for another import type's item that names no one", async () => {
    listImportBatches.mockResolvedValue([batch({ import_type: "employee_master_xlsx" })]);
    listImportItems.mockResolvedValue([{ ...NO_TARGET, source_ref: "row_2" }]);
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));
    await screen.findByText("Sasha Quill");
    expect(within(itemsSection()).getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("reads the items again after a 409 target changed, and says why", async () => {
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);
    const reads = listImportItems.mock.calls.length;
    approveImportItem.mockRejectedValue(
      new apiClient.ApiError(409, {
        error: { code: "IMPORT_ITEM_TARGET_CHANGED", message: "The employee changed." },
      }),
    );
    listImportItems.mockResolvedValue([
      item(),
      {
        ...NEEDS_REVIEW,
        target_employee: {
          ...NEEDS_REVIEW.target_employee!,
          current_title: "Senior Surveyor",
          fingerprint: "fp-2",
          changes: NEEDS_REVIEW.target_employee!.changes!.map((entry) =>
            entry.field === "current_title" ? { ...entry, on_file: "Senior Surveyor" } : entry,
          ),
        },
      },
      FAILED,
    ]);

    const row = within(itemsSection()).getAllByRole("row")[2];
    await user.click(within(row).getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The employee changed after this item was loaded",
    );
    await waitFor(() => expect(listImportItems.mock.calls.length).toBeGreaterThan(reads));
    await screen.findByText("Senior Surveyor → Surveyor (changes)");
    // The next Approve sends the fingerprint of what is shown now.
    approveImportItem.mockResolvedValue(NEEDS_REVIEW);
    const reloaded = within(itemsSection()).getAllByRole("row")[2];
    await user.click(within(reloaded).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(approveImportItem).toHaveBeenLastCalledWith("i2", "fp-2"));
  });

  it("shows another coded refusal's message", async () => {
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);
    approveImportItem.mockRejectedValue(
      new apiClient.ApiError(409, {
        error: {
          code: "EMPLOYEE_ARCHIVED",
          message: "The matched employee is archived (removed from the roster).",
        },
      }),
    );
    const row = within(itemsSection()).getAllByRole("row")[2];
    await user.click(within(row).getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Approve failed: The matched employee is archived (removed from the roster).",
    );
  });

  it("renders the API's change list as it is, computing nothing", async () => {
    // The payload's title differs from what is on file, but the API says the
    // title does not change (and an email differing only in case is kept):
    // the panel shows what the API says, because the merge writes that.
    listImportItems.mockResolvedValue([
      {
        ...NEEDS_REVIEW,
        target_employee: {
          ...NEEDS_REVIEW.target_employee!,
          changes: [
            {
              field: "email",
              on_file: "rowan@example.test",
              from_resume: "Rowan@Example.test",
              changes: false,
            },
            {
              field: "current_title",
              on_file: "Junior Surveyor",
              from_resume: "Surveyor",
              changes: false,
            },
            { field: "professional_location", on_file: "Pune", from_resume: "Goa", changes: true },
            { field: "years_experience", on_file: 2, from_resume: 3, changes: true },
          ],
        },
      },
    ]);
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));
    const changes = await screen.findByLabelText("Changes on approval");
    expect(changes).toHaveTextContent("rowan@example.test (kept)");
    expect(changes).toHaveTextContent("Junior Surveyor (kept)");
    expect(changes).toHaveTextContent("Pune → Goa (changes)");
    expect(changes).toHaveTextContent("2 years → 3 years (changes)");
    // In the API's order.
    expect(
      within(changes)
        .getAllByRole("term")
        .map((t) => t.textContent),
    ).toEqual(["Email", "Title", "Location", "Experience", "Resume"]);
  });

  it("offers only Reject when the resume's fields could not be read", async () => {
    listImportItems.mockResolvedValue([
      { ...NEEDS_REVIEW, target_employee: { ...NEEDS_REVIEW.target_employee!, changes: null } },
    ]);
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));
    await screen.findByText("Rowan Quill");
    const row = within(itemsSection()).getAllByRole("row")[1];
    expect(row).toHaveTextContent("This resume's extracted fields could not be read");
    expect(within(row).queryByLabelText("Changes on approval")).toBeNull();
    expect(
      within(row)
        .queryAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Reject"]);
  });

  it("names the employee an approved resume went into", async () => {
    listImportItems.mockResolvedValue([
      {
        ...NEEDS_REVIEW,
        status: "approved",
        target_employee: { ...NEEDS_REVIEW.target_employee!, merged_into_existing: true },
      },
      item({
        id: "i5",
        target_knowledge_item_id: "e2",
        target_employee: {
          ...NEEDS_REVIEW.target_employee!,
          knowledge_item_id: "e2",
          name: "Avery Q.",
          merged_into_existing: false,
        },
      }),
    ]);
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));
    await screen.findByText("Rowan Quill");
    const [merged, created] = within(itemsSection()).getAllByRole("row").slice(1);
    expect(merged).toHaveTextContent("Merged into Rowan A. Quill");
    expect(created).toHaveTextContent("Employee: Avery Q.");
    expect(within(merged).queryByLabelText("Changes on approval")).toBeNull();
  });
});

// Plan item 111: a resume item in review lists the employees it matches, each
// with its change list and a "Merge into NAME" button, and "Import as a new
// person" unless a strong signal matched. Neither name contains "Approve":
// full-flow.spec.ts clicks any button whose name contains it.
describe("plan item 111: pick the employee or import as a new person", () => {
  function candidate(
    overrides: Partial<apiClient.ImportItemCandidate> = {},
  ): apiClient.ImportItemCandidate {
    return {
      knowledge_item_id: "e7",
      name: "Sasha Quill",
      email: "sasha.one@example.test",
      current_title: "Analyst",
      professional_location: null,
      years_experience: null,
      resume_document_id: null,
      profile_review_status: "verified",
      archived: false,
      signals: ["name"],
      fingerprint: "fp-e7",
      changes: [
        {
          field: "email",
          on_file: "sasha.one@example.test",
          from_resume: null,
          changes: false,
          held_by_other: false,
        },
        {
          field: "current_title",
          on_file: "Analyst",
          from_resume: "Lead Analyst",
          changes: true,
          held_by_other: false,
        },
        {
          field: "professional_location",
          on_file: null,
          from_resume: null,
          changes: false,
          held_by_other: false,
        },
        {
          field: "years_experience",
          on_file: null,
          from_resume: null,
          changes: false,
          held_by_other: false,
        },
      ],
      ...overrides,
    };
  }

  const TWO_MATCHES = item({
    id: "i5",
    source_ref: "0ae13cd8-1525-4c67-af84-83532c24d8f0/CV.docx",
    status: "needs_review",
    error_message: "normalized name matched without a strong identity signal",
    extracted_payload: { name: "Sasha Quill", current_title: "Lead Analyst", confidence: 0.7 },
    candidates: {
      fingerprint: "cands-1",
      strong_match: false,
      candidates: [
        candidate(),
        candidate({
          knowledge_item_id: "e8",
          fingerprint: "fp-e8",
          email: "sasha.two@example.test",
        }),
      ],
    },
  });

  async function openRow(user: ReturnType<typeof userEvent.setup>) {
    renderScreen();
    await user.click(await screen.findByRole("button", { name: "Review items" }));
    await screen.findAllByText("Sasha Quill");
    return within(itemsSection()).getAllByRole("row")[1];
  }

  it("lists each candidate with Merge into, and Import as a new person, and no Approve", async () => {
    listImportItems.mockResolvedValue([TWO_MATCHES]);
    const row = await openRow(userEvent.setup());
    expect(
      within(row)
        .queryAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "Merge into Sasha Quill (sasha.one@example.test)",
      "Merge into Sasha Quill (sasha.two@example.test)",
      "Import as a new person",
      "Reject",
    ]);
    expect(within(row).queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(row).toHaveTextContent("Choose the one it belongs to");
    expect(within(row).getAllByText("Analyst → Lead Analyst (changes)")).toHaveLength(2);
  });

  it("a pick sends the candidate, its fingerprint and the list's fingerprint", async () => {
    listImportItems.mockResolvedValue([TWO_MATCHES]);
    approveImportItem.mockResolvedValue(TWO_MATCHES);
    const user = userEvent.setup();
    const row = await openRow(user);
    await user.click(
      within(row).getByRole("button", { name: "Merge into Sasha Quill (sasha.two@example.test)" }),
    );
    await waitFor(() =>
      expect(approveImportItem).toHaveBeenCalledWith("i5", "fp-e8", {
        employeeId: "e8",
        candidatesFingerprint: "cands-1",
      }),
    );
  });

  it("names each same-name candidate's button apart: email, else title, else position", async () => {
    listImportItems.mockResolvedValue([
      {
        ...TWO_MATCHES,
        candidates: {
          ...TWO_MATCHES.candidates!,
          candidates: [
            candidate({ knowledge_item_id: "e7", email: null, current_title: "Analyst" }),
            candidate({ knowledge_item_id: "e8", email: null, current_title: "Surveyor" }),
            candidate({ knowledge_item_id: "e9", email: null, current_title: null }),
            candidate({ knowledge_item_id: "e10", email: null, current_title: null }),
            candidate({ knowledge_item_id: "e11", name: "Ada Quill", email: "ada@example.test" }),
          ],
        },
      },
    ]);
    const row = await openRow(userEvent.setup());
    const names = within(row)
      .getAllByRole("button", { name: /^Merge into/ })
      .map((b) => b.textContent);
    expect(names).toEqual([
      "Merge into Sasha Quill (Analyst)",
      "Merge into Sasha Quill (Surveyor)",
      "Merge into Sasha Quill #3",
      "Merge into Sasha Quill #4",
      "Merge into Ada Quill",
    ]);
    expect(new Set(names).size).toBe(names.length);
  });

  it("Import as a new person sends the list's fingerprint", async () => {
    listImportItems.mockResolvedValue([TWO_MATCHES]);
    importItemAsNewEmployee.mockResolvedValue(TWO_MATCHES);
    const user = userEvent.setup();
    const row = await openRow(user);
    await user.click(within(row).getByRole("button", { name: "Import as a new person" }));
    await waitFor(() => expect(importItemAsNewEmployee).toHaveBeenCalledWith("i5", "cands-1"));
  });

  it("offers no Import as a new person when a strong signal matched, and says why", async () => {
    listImportItems.mockResolvedValue([
      { ...TWO_MATCHES, candidates: { ...TWO_MATCHES.candidates!, strong_match: true } },
    ]);
    const row = await openRow(userEvent.setup());
    expect(within(row).queryByRole("button", { name: "Import as a new person" })).toBeNull();
    expect(row).toHaveTextContent("cannot be imported as a new person");
  });

  it("offers no Merge into an archived candidate", async () => {
    listImportItems.mockResolvedValue([
      {
        ...TWO_MATCHES,
        candidates: { ...TWO_MATCHES.candidates!, candidates: [candidate({ archived: true })] },
      },
    ]);
    const row = await openRow(userEvent.setup());
    expect(within(row).queryByRole("button", { name: /Merge into/ })).toBeNull();
    expect(row).toHaveTextContent("restore this employee before merging");
  });

  it("shows an email another employee holds as kept, and why", async () => {
    const held = candidate();
    listImportItems.mockResolvedValue([
      {
        ...TWO_MATCHES,
        candidates: {
          ...TWO_MATCHES.candidates!,
          candidates: [
            {
              ...held,
              changes: held.changes.map((entry) =>
                entry.field === "email"
                  ? { ...entry, from_resume: "jordan@example.test", held_by_other: true }
                  : entry,
              ),
            },
          ],
        },
      },
    ]);
    const row = await openRow(userEvent.setup());
    expect(row).toHaveTextContent(
      "sasha.one@example.test (kept: another employee has jordan@example.test)",
    );
  });

  it("reads the items again after a 409 candidates changed, and says why", async () => {
    listImportItems.mockResolvedValue([TWO_MATCHES]);
    importItemAsNewEmployee.mockRejectedValue(
      new apiClient.ApiError(409, {
        error: { code: "IMPORT_ITEM_CANDIDATES_CHANGED", message: "The candidates changed." },
      }),
    );
    const user = userEvent.setup();
    const row = await openRow(user);
    const reads = listImportItems.mock.calls.length;
    await user.click(within(row).getByRole("button", { name: "Import as a new person" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The matching employees changed after this item was loaded",
    );
    await waitFor(() => expect(listImportItems.mock.calls.length).toBeGreaterThan(reads));
  });

  it("offers no decision on an item that is no longer in review", async () => {
    // The API sends no candidates for a decided item; the row offers no
    // decision even if a stale read still carried them.
    listImportItems.mockResolvedValue([{ ...TWO_MATCHES, status: "approved" }]);
    const row = await openRow(userEvent.setup());
    expect(within(row).queryAllByRole("button")).toHaveLength(0);
  });

  it("keeps Approve for the employee the item names and lists only the others", async () => {
    const named = candidate({ knowledge_item_id: "e1", name: "Rowan A. Quill" });
    listImportItems.mockResolvedValue([
      {
        ...NEEDS_REVIEW,
        candidates: {
          fingerprint: "cands-2",
          strong_match: true,
          candidates: [named, candidate({ knowledge_item_id: "e9", name: "Rowan B. Quill" })],
        },
      },
    ]);
    const user = userEvent.setup();
    renderScreen();
    await openItems(user);
    const row = within(itemsSection()).getAllByRole("row")[1];
    expect(
      within(row)
        .queryAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["Merge into Rowan B. Quill", "Approve", "Reject"]);
    expect(row).toHaveTextContent("It also matches");
  });
});
