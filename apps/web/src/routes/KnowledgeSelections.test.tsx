import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { KnowledgeItemResponse, KnowledgeSelectionResponse } from "../api/client";
import { KnowledgeSelections } from "./KnowledgeSelections";

// Restyle batch 4. Pins the impact-preview DOM that full-flow.spec.ts
// :1222-1327 reads - above all :1253 (every <li> on the page counted) and
// :1258 (every `ul li` JSON.parsed) - plus the new badges and summary.
// Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    listOrganizationKnowledge: vi.fn(),
    listKnowledgeSelections: vi.fn(),
    previewGraphImpact: vi.fn(),
    setKnowledgeSelection: vi.fn(),
    triggerGraphProjection: vi.fn(),
  };
});

vi.mock("../auth/useAuth", () => ({
  useAuth: () => ({ organizationId: "org-1" }),
}));

const listOrganizationKnowledge = vi.mocked(apiClient.listOrganizationKnowledge);
const listKnowledgeSelections = vi.mocked(apiClient.listKnowledgeSelections);
const previewGraphImpact = vi.mocked(apiClient.previewGraphImpact);
const setKnowledgeSelection = vi.mocked(apiClient.setKnowledgeSelection);

beforeEach(() => {
  listOrganizationKnowledge.mockResolvedValue([item()]);
  listKnowledgeSelections.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function item(): KnowledgeItemResponse {
  return {
    id: "k1",
    organization_id: "org-1",
    item_type: "past_project",
    title: "Dana Whitfield",
    status: "approved",
    valid_from: null,
    valid_until: null,
    source_document_id: null,
    source_chunk_id: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function selection(selected: boolean): KnowledgeSelectionResponse {
  return {
    id: "s1",
    tender_id: "t1",
    knowledge_item_id: "k1",
    selected,
    selected_at: null,
    selected_by: null,
    deselected_at: null,
    deselected_by: null,
    change_reason: null,
  };
}

const ROWS = [
  { labels: ["Requirement"], affected: { id: "req-1", display_code: "STAFF-001" } },
  { labels: ["Compliance"], affected: { id: "comp-1" } },
  { labels: ["Requirement"], affected: { id: "req-2" } },
];

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/knowledge-selections"]}>
      <Routes>
        <Route path="/tenders/:tenderId/knowledge-selections" element={<KnowledgeSelections />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("KnowledgeSelections (restyle batch 4)", () => {
  it("renders no <li> at all until a preview is open (full-flow.spec.ts:1253)", async () => {
    const { container } = renderScreen();
    await screen.findByText("Dana Whitfield");
    expect(container.querySelectorAll("li")).toHaveLength(0);
    expect(container.querySelector("table tbody tr td")!.textContent).toBe("Dana Whitfield");
    expect(screen.getByText("Past project")).toBeInTheDocument();
    expect(screen.getByText("Not selected")).toBeInTheDocument();
  });

  it("gives one <li> per row whose text JSON.parses back to the row, plus a summary outside the list (full-flow.spec.ts:1253, :1258-1272)", async () => {
    const user = userEvent.setup();
    previewGraphImpact.mockResolvedValue(ROWS);
    const { container } = renderScreen();

    await user.click(await screen.findByRole("button", { name: "Preview impact" }));
    await screen.findByRole("heading", { name: "Impact preview" });
    await waitFor(() => expect(container.querySelectorAll("li")).toHaveLength(ROWS.length));
    const texts = Array.from(container.querySelectorAll("ul li")).map((li) => li.textContent);
    expect(texts).toEqual(ROWS.map((row) => JSON.stringify(row)));
    expect(texts.map((t) => JSON.parse(t!))).toEqual(ROWS);
    const summary = screen.getByText("3 downstream facts affected: Requirement 2, Compliance 1");
    expect(summary.tagName).toBe("P");
    expect(summary.closest("ul")).toBeNull();
    // Polish pass (P2): the previewed item is named in a <p>, not the
    // heading (its name stays exactly "Impact preview") and not an <li>.
    const named = screen.getByText("For Dana Whitfield");
    expect(named.tagName).toBe("P");
    expect(screen.getByRole("heading", { name: "Impact preview" }).textContent).toBe(
      "Impact preview",
    );
    expect(container.querySelectorAll("li")).toHaveLength(ROWS.length);
  });

  it("keeps the empty sentence verbatim and shows no summary or list for no rows (full-flow.spec.ts:1254)", async () => {
    const user = userEvent.setup();
    previewGraphImpact.mockResolvedValue([]);
    const { container } = renderScreen();

    await user.click(await screen.findByRole("button", { name: "Preview impact" }));
    expect(await screen.findByText("No downstream facts would be affected.")).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(0);
    expect(screen.queryByText(/downstream facts? affected/)).toBeNull();
  });

  it("labels the confirm button exactly 'Confirm select', then 'Confirm deselect' once selected (full-flow.spec.ts:1323, :1325)", async () => {
    const user = userEvent.setup();
    previewGraphImpact.mockResolvedValue([]);
    setKnowledgeSelection.mockResolvedValue(selection(true));
    renderScreen();

    await user.click(await screen.findByRole("button", { name: "Preview impact" }));
    const confirm = await screen.findByRole("button", { name: /^Confirm (select|deselect)$/ });
    expect(confirm.textContent).toBe("Confirm select");

    listKnowledgeSelections.mockResolvedValue([selection(true)]);
    await user.click(confirm);
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Impact preview" })).toBeNull(),
    );
    const row = document.querySelector("table tbody tr") as HTMLElement;
    expect(within(row).getByText("Selected")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Preview impact" }));
    const again = await screen.findByRole("button", { name: /^Confirm (select|deselect)$/ });
    expect(again.textContent).toBe("Confirm deselect");
  });

  it("marks the previewed row's button aria-busy while the preview loads", async () => {
    const user = userEvent.setup();
    let resolve!: (rows: typeof ROWS) => void;
    previewGraphImpact.mockReturnValue(new Promise((r) => (resolve = r)));
    renderScreen();

    const button = await screen.findByRole("button", { name: "Preview impact" });
    await user.click(button);
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
    resolve(ROWS);
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute("aria-busy");
  });

  it("shows the empty state only after the library answers empty", async () => {
    listOrganizationKnowledge.mockResolvedValue([]);
    renderScreen();
    expect(await screen.findByText("No company knowledge yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Talent" })).toHaveAttribute("href", "/talent");
  });
});
