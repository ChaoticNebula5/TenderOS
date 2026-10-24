import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { KnowledgeItemResponse } from "../api/client";
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
  };
});

vi.mock("../auth/useAuth", () => ({
  useAuth: () => ({ organizationId: "org-1" }),
}));

const listOrganizationKnowledge = vi.mocked(apiClient.listOrganizationKnowledge);

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
    vi.mocked(apiClient.uploadKnowledgeDocument).mockResolvedValue(undefined);
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
