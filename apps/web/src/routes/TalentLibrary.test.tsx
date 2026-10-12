import { cleanup, render, screen, within } from "@testing-library/react";
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

  it("shows types in words and a date-only valid-until, or Not set", async () => {
    const { container } = renderScreen();
    await screen.findByText("Harbour Systems");
    const pmp = screen.getByText("PMP").closest("tr")!;
    expect(within(pmp).getByText("Certification")).toBeInTheDocument();
    expect(within(pmp).getByText("31 Mar 2025")).toBeInTheDocument();
    const project = screen.getByText("Harbour Systems").closest("tr")!;
    expect(within(project).getByText("Past project")).toBeInTheDocument();
    expect(within(project).getByText("Not set")).toBeInTheDocument();
    for (const raw of ["past_project", "company_document"]) {
      expect(container.querySelector("tbody")!.textContent).not.toContain(raw);
    }
    expect(screen.getByText("5 items, 2 employees")).toBeInTheDocument();
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
