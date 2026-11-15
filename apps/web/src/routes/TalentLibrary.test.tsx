import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { ImportBatchResponse, KnowledgeItemResponse } from "../api/client";
import { TalentLibrary } from "./TalentLibrary";

// Restyle batch 6. Pins what full-flow.spec.ts :476-492 reads - one
// a[href^="/talent/employees/"] per employee, href shape unchanged - plus
// the restyle's own promises. Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listOrganizationKnowledge: vi.fn(),
    patchKnowledgeItem: vi.fn(),
    uploadKnowledgeDocument: vi.fn(),
    getImportBatch: vi.fn(),
  };
});

vi.mock("../auth/useAuth", () => ({
  useAuth: () => ({ organizationId: "org-1" }),
}));

const listOrganizationKnowledge = vi.mocked(apiClient.listOrganizationKnowledge);
const uploadKnowledgeDocument = vi.mocked(apiClient.uploadKnowledgeDocument);
const getImportBatch = vi.mocked(apiClient.getImportBatch);

// Plan item 78: the capability statement's upload answers with its batch.
function capabilityBatch(overrides: Partial<ImportBatchResponse> = {}): ImportBatchResponse {
  return {
    id: "cap-batch",
    organization_id: "org-1",
    import_type: "manual",
    source_document_id: "doc-cap",
    status: "queued",
    total_items: 0,
    completed_items: 0,
    failed_items: 0,
    created_at: "2026-10-10T08:00:00Z",
    ...overrides,
  };
}

function ki(
  id: string,
  item_type: string,
  title: string,
  overrides: Partial<KnowledgeItemResponse> = {},
): KnowledgeItemResponse {
  return {
    id,
    organization_id: "org-1",
    item_type,
    title,
    status: "active",
    valid_from: null,
    valid_until: null,
    source_document_id: null,
    source_chunk_id: null,
    created_at: "2026-10-10T08:00:00Z",
    updated_at: "2026-10-10T08:00:00Z",
    ...overrides,
  };
}

const ITEMS = [
  ki("k1", "employee", "Avery Quill"),
  ki("k2", "employee", "Rowan Quill", { status: "unverified" }),
  ki("k3", "certification", "PMP", { status: "expired", valid_until: "2025-03-31" }),
  ki("k4", "past_project", "Harbour Systems"),
  ki("k5", "company_document", "Capability statement", { status: "archived" }),
  ki("k6", "certification", "ISO 9001 Lead Auditor"),
];

beforeEach(() => {
  listOrganizationKnowledge.mockResolvedValue(ITEMS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderScreen() {
  return render(
    <MemoryRouter>
      <TalentLibrary />
    </MemoryRouter>,
  );
}

describe("TalentLibrary (restyle batch 6)", () => {
  it("has exactly one employee link per employee row, none elsewhere (full-flow.spec.ts:476-492)", async () => {
    const { container } = renderScreen();
    await screen.findByText("Harbour Systems");

    const links = Array.from(container.querySelectorAll('a[href^="/talent/employees/"]'));
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/talent/employees/k1",
      "/talent/employees/k2",
    ]);
    for (const row of Array.from(container.querySelectorAll("tbody tr"))) {
      const isEmployee = ["Avery Quill", "Rowan Quill"].includes(
        row.querySelector("td")!.textContent ?? "",
      );
      expect(row.querySelectorAll('a[href^="/talent/employees/"]')).toHaveLength(
        isEmployee ? 1 : 0,
      );
    }
  });

  it("shows status as a badge plus a named select with raw values and mapped labels", async () => {
    renderScreen();
    const select = (await screen.findByRole("combobox", {
      name: "Status of PMP",
    })) as HTMLSelectElement;
    expect(select.value).toBe("expired");
    expect(Array.from(select.options).map((o) => [o.value, o.textContent])).toEqual([
      ["active", "Active"],
      ["expired", "Expired"],
      ["archived", "Archived"],
      ["unverified", "Unverified"],
    ]);
    const cell = select.closest("td")!;
    expect(cell.querySelector(".status-badge--failed")).toHaveTextContent("Expired");
    const unverified = screen
      .getByRole("combobox", { name: "Status of Rowan Quill" })
      .closest("td")!;
    expect(unverified.querySelector(".status-badge--warning")).toHaveTextContent("Unverified");
  });

  // Polish pass (decided): Valid until is blank where it does not apply
  // (only certifications carry one); "Not set" only for a certification
  // without one.
  it("shows types in words, a date-only valid-until, Not set only for a certification without one", async () => {
    const { container } = renderScreen();
    await screen.findByText("Harbour Systems");
    const pmp = screen.getByText("PMP").closest("tr")!;
    expect(within(pmp).getByText("Certification")).toBeInTheDocument();
    expect(within(pmp).getByText("31 Mar 2025")).toBeInTheDocument();
    const project = screen.getByText("Harbour Systems").closest("tr")!;
    expect(within(project).getByText("Past project")).toBeInTheDocument();
    expect(project.querySelectorAll("td")[3].textContent).toBe("");
    const iso = screen.getByText("ISO 9001 Lead Auditor").closest("tr")!;
    expect(iso.querySelectorAll("td")[3].textContent).toBe("Not set");
    expect(screen.getAllByText("Not set")).toHaveLength(1);
    for (const raw of ["past_project", "company_document"]) {
      expect(container.querySelector("tbody")!.textContent).not.toContain(raw);
    }
    expect(screen.getByText("6 items, 2 employees")).toBeInTheDocument();
  });

  it("renders no rows while loading, and the empty state only after an empty response", async () => {
    let resolve!: (items: KnowledgeItemResponse[]) => void;
    listOrganizationKnowledge.mockReturnValue(new Promise((r) => (resolve = r)));
    const { container } = renderScreen();

    const table = container.querySelector("table")!;
    expect(table.getAttribute("aria-busy")).toBe("true");
    expect(table.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(screen.queryByText("Nothing in the library yet")).toBeNull();

    resolve([]);
    expect(await screen.findByText("Nothing in the library yet")).toBeInTheDocument();
    expect(table.hasAttribute("aria-busy")).toBe(false);
    expect(container.querySelectorAll('a[href^="/talent/employees/"]')).toHaveLength(0);
  });

  it("labels the capability statement upload", () => {
    renderScreen();
    const input = screen.getByLabelText("Capability statement") as HTMLInputElement;
    expect(input.type).toBe("file");
  });

  it("shows a failed load as a notice", async () => {
    listOrganizationKnowledge.mockRejectedValue(new Error("down"));
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load the knowledge library",
    );
  });
});

// Upload-list race, sibling screens (useListReads.ts): the re-read after a
// capability-statement upload can be in flight when a status change lands.
// Its answer predates the change and must not put the old status back,
// while the item it adds must still appear.
describe("a status change that lands while the re-read after an upload is in flight", () => {
  it("keeps the new status, and the upload's new item still appears", async () => {
    const pmp = ki("k3", "certification", "PMP", { status: "expired", valid_until: "2025-03-31" });
    let answerReread: (rows: KnowledgeItemResponse[]) => void = () => {};
    listOrganizationKnowledge.mockReset();
    listOrganizationKnowledge.mockResolvedValueOnce([pmp]).mockReturnValueOnce(
      new Promise<KnowledgeItemResponse[]>((r) => {
        answerReread = r;
      }),
    );
    uploadKnowledgeDocument.mockResolvedValue(capabilityBatch());
    getImportBatch.mockResolvedValue(capabilityBatch());
    vi.mocked(apiClient.patchKnowledgeItem).mockResolvedValue({ ...pmp, status: "active" });
    const { container } = renderScreen();

    const select = (await screen.findByRole("combobox", {
      name: "Status of PMP",
    })) as HTMLSelectElement;
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "capability.pdf")] } });
    await waitFor(() => expect(listOrganizationKnowledge).toHaveBeenCalledTimes(2));
    fireEvent.change(select, { target: { value: "active" } });
    await waitFor(() =>
      expect(
        (screen.getByRole("combobox", { name: "Status of PMP" }) as HTMLSelectElement).value,
      ).toBe("active"),
    );

    answerReread([pmp, ki("k9", "past_project", "Harbour Systems")]);

    expect(await screen.findByText("Harbour Systems")).toBeInTheDocument();
    expect(
      (screen.getByRole("combobox", { name: "Status of PMP" }) as HTMLSelectElement).value,
    ).toBe("active");
  });
});

describe("plan item 78: a capability statement's projects appear without a reload", () => {
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

  function upload(container: HTMLElement) {
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "capability.pdf")] } });
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks for no reload any more", async () => {
    renderScreen();
    await advance(0);
    expect(screen.queryByText(/reload/i)).not.toBeInTheDocument();
  });

  it("follows the upload's batch, then reads the library once it is done", async () => {
    listOrganizationKnowledge.mockReset();
    listOrganizationKnowledge.mockResolvedValue([ki("k1", "employee", "Avery Quill")]);
    uploadKnowledgeDocument.mockResolvedValue(capabilityBatch());
    getImportBatch
      .mockResolvedValueOnce(capabilityBatch({ status: "processing", total_items: 2 }))
      .mockResolvedValue(
        capabilityBatch({ status: "completed", total_items: 2, completed_items: 2 }),
      );
    const { container } = renderScreen();
    await advance(0);

    upload(container);
    await advance(0);
    expect(screen.getByText("Importing capability.pdf...")).toBeInTheDocument();
    expect(screen.queryByText("Harbour Systems")).not.toBeInTheDocument();

    listOrganizationKnowledge.mockResolvedValue([
      ki("k1", "employee", "Avery Quill"),
      ki("k4", "past_project", "Harbour Systems"),
    ]);
    await advance(2_000);
    expect(getImportBatch).toHaveBeenCalledWith("cap-batch");
    expect(screen.getByText("Importing capability.pdf...")).toBeInTheDocument();
    await advance(2_000);

    expect(screen.getByText("Harbour Systems")).toBeInTheDocument();
    expect(screen.queryByText("Importing capability.pdf...")).not.toBeInTheDocument();
    const batchReads = getImportBatch.mock.calls.length;
    await advance(10 * 60_000);
    expect(getImportBatch).toHaveBeenCalledTimes(batchReads);
  });

  it("says so when the import failed", async () => {
    uploadKnowledgeDocument.mockResolvedValue(capabilityBatch());
    getImportBatch.mockResolvedValue(
      capabilityBatch({ status: "failed", total_items: 1, failed_items: 1 }),
    );
    const { container } = renderScreen();
    await advance(0);

    upload(container);
    await advance(0);
    await advance(2_000);

    expect(screen.getByText("Importing capability.pdf failed.")).toBeInTheDocument();
    expect(screen.queryByText("Importing capability.pdf...")).not.toBeInTheDocument();
  });
});
