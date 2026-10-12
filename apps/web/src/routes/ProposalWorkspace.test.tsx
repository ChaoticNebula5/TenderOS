import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type {
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
  };
});

const listProposalSections = vi.mocked(apiClient.listProposalSections);
const listProposalClaims = vi.mocked(apiClient.listProposalClaims);
const traceProposalClaim = vi.mocked(apiClient.traceProposalClaim);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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
