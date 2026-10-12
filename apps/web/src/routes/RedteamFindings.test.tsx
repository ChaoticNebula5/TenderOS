import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { RedteamFindingResponse } from "../api/client";
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
  };
});

const listRedteamFindings = vi.mocked(apiClient.listRedteamFindings);
const resolveRedteamFinding = vi.mocked(apiClient.resolveRedteamFinding);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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
});
