import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { ClaimEvidenceTraceResponse, ProposalClaimTraceResponse } from "../api/client";
import { TraceabilityDrawer } from "./TraceabilityDrawer";

// Restyle batch 5. The drawer is a modal dialog: these pin its focus
// behaviour (keyboard pass, DESIGN.md Accessibility) and the DOM that
// full-flow.spec.ts:980-983 reads. Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return { ...actual, traceProposalClaim: vi.fn() };
});

const traceProposalClaim = vi.mocked(apiClient.traceProposalClaim);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function evidence(id: string, overrides: Partial<ClaimEvidenceTraceResponse> = {}) {
  return {
    evidence_link_id: id,
    source_kind: "past_project",
    excerpt: `Excerpt ${id}.`,
    verified: true,
    validity_status: "valid",
    knowledge_item_id: null,
    knowledge_item_title: "Port Expansion 2024",
    document_id: null,
    document_filename: null,
    ...overrides,
  };
}

function trace(overrides: Partial<ProposalClaimTraceResponse> = {}): ProposalClaimTraceResponse {
  return {
    claim_id: "c1",
    claim_text: "The bidder delivered a comparable port.",
    claim_type: "experience",
    is_factual: true,
    grounding_status: "needs_review",
    section_id: "s1",
    section_name: "Relevant experience",
    requirement_ids: [],
    employee_knowledge_item_ids: [],
    evidence: [evidence("e1")],
    currently_in_scope_evidence: [],
    section_is_data_gated: true,
    ...overrides,
  };
}

// A trigger outside the drawer, focused before it opens - stands in for the
// "Inspect trace" button.
function renderWithTrigger(onClose = vi.fn()) {
  const view = render(
    <div>
      <button type="button">Inspect trace</button>
      <TraceabilityDrawer
        claimId="c1"
        onClose={onClose}
        returnFocusTo={document.querySelector("button")}
      />
    </div>,
  );
  return { ...view, onClose };
}

describe("TraceabilityDrawer (restyle batch 5)", () => {
  it("is the page's only <aside>, a labelled modal dialog, and moves focus to Close on open", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    const { container } = render(<TraceabilityDrawer claimId="c1" onClose={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Claim trace" });
    expect(dialog.tagName).toBe("ASIDE");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(container.querySelectorAll("aside")).toHaveLength(1);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
  });

  it("shows the 'Claim trace' heading only once the trace has loaded (full-flow.spec.ts:981-987)", async () => {
    let resolve!: (t: ProposalClaimTraceResponse) => void;
    traceProposalClaim.mockReturnValue(new Promise((r) => (resolve = r)));
    render(<TraceabilityDrawer claimId="c1" onClose={vi.fn()} />);

    expect(screen.getByText("Loading trace...")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Claim trace" })).toBeNull();
    resolve(trace());
    expect(await screen.findByRole("heading", { name: "Claim trace" })).toBeInTheDocument();
    // The in-scope result is already there when the heading appears.
    expect(screen.getByText("No additional in-scope evidence.")).toBeInTheDocument();
    // Polish pass (P5): the heading shares the bar with Close, inside the aside.
    const heading = screen.getByRole("heading", { name: "Claim trace" });
    expect(heading.parentElement).toBe(screen.getByRole("button", { name: "Close" }).parentElement);
    expect(heading.closest("aside")).not.toBeNull();
  });

  // Polish pass (P5): the page behind does not scroll while the drawer is
  // open, and its own styles come back exactly on close.
  it("locks page scroll while open and restores it on close", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    const html = document.documentElement;
    html.style.overflow = "auto";
    html.style.paddingRight = "";
    const { unmount } = render(<TraceabilityDrawer claimId="c1" onClose={vi.fn()} />);
    await screen.findByRole("heading", { name: "Claim trace" });
    expect(html.style.overflow).toBe("hidden");
    // jsdom lays nothing out (clientWidth 0), so no scrollbar width is added.
    expect(html.style.paddingRight).toBe("");
    unmount();
    expect(html.style.overflow).toBe("auto");
    html.style.overflow = "";
  });

  it("keeps Tab and Shift+Tab inside the drawer", async () => {
    const user = userEvent.setup();
    traceProposalClaim.mockResolvedValue(trace());
    renderWithTrigger();

    await screen.findByRole("heading", { name: "Claim trace" });
    const close = screen.getByRole("button", { name: "Close" });
    const dialog = screen.getByRole("dialog");
    close.focus();
    // Close is the only focusable element: Tab and Shift+Tab both stay on it.
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "Inspect trace" }));
  });

  // Focus return itself is pinned below (unmount) and end to end in
  // ProposalWorkspace.test.tsx (the exact Inspect trace button).
  it("asks to close on Escape", async () => {
    const user = userEvent.setup();
    traceProposalClaim.mockResolvedValue(trace());
    const { onClose } = renderWithTrigger();

    await screen.findByRole("heading", { name: "Claim trace" });
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the uncited panel an alert containing 'not cited' (full-flow.spec.ts:982)", async () => {
    traceProposalClaim.mockResolvedValue(
      trace({ currently_in_scope_evidence: [evidence("e2"), evidence("e3")] }),
    );
    render(<TraceabilityDrawer claimId="c1" onClose={vi.fn()} />);

    await screen.findByRole("heading", { name: "Claim trace" });
    const alerts = screen.getAllByRole("alert").filter((a) => a.textContent?.includes("not cited"));
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("2 pieces of in-scope evidence not cited");
    expect(screen.queryByText("No additional in-scope evidence.")).toBeNull();
  });

  it("shows evidence without a raw source_kind prefix, with verified and validity badges", async () => {
    traceProposalClaim.mockResolvedValue(
      trace({
        evidence: [evidence("e1", { verified: false, validity_status: "expired" })],
      }),
    );
    const { container } = render(<TraceabilityDrawer claimId="c1" onClose={vi.fn()} />);

    await screen.findByText("Excerpt e1.");
    expect(container.textContent).not.toContain("past_project");
    expect(screen.getByText("Past project · Port Expansion 2024")).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
  });

  it("returns focus to the trigger when unmounted", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    const trigger = document.createElement("button");
    trigger.textContent = "Inspect trace";
    document.body.appendChild(trigger);
    trigger.focus();
    const { unmount } = render(
      <TraceabilityDrawer claimId="c1" onClose={vi.fn()} returnFocusTo={trigger} />,
    );
    await waitFor(() => expect(document.activeElement).not.toBe(trigger));
    unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });

  // Batch 5 fix 1: keyboard handling must not depend on focus being inside
  // the panel - a click on the scrim drops focus to <body>.
  it("closes when the scrim is clicked", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    const onClose = vi.fn();
    const { container } = render(<TraceabilityDrawer claimId="c1" onClose={onClose} />);

    await screen.findByRole("heading", { name: "Claim trace" });
    const scrim = container.querySelector(".trace-drawer__scrim")!;
    expect(scrim).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(scrim);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape even when focus has left the panel for <body>", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    const onClose = vi.fn();
    render(<TraceabilityDrawer claimId="c1" onClose={onClose} />);

    await screen.findByRole("heading", { name: "Claim trace" });
    (document.activeElement as HTMLElement).blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("pulls focus back into the dialog on Tab and Shift+Tab when focus is on <body>", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    render(<TraceabilityDrawer claimId="c1" onClose={vi.fn()} />);

    await screen.findByRole("heading", { name: "Claim trace" });
    const dialog = screen.getByRole("dialog");
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document.body, { key: "Tab" });
    expect(dialog.contains(document.activeElement)).toBe(true);
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document.body, { key: "Tab", shiftKey: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("removes its document listener on unmount", async () => {
    traceProposalClaim.mockResolvedValue(trace());
    const onClose = vi.fn();
    const { unmount } = render(<TraceabilityDrawer claimId="c1" onClose={onClose} />);

    await screen.findByRole("heading", { name: "Claim trace" });
    unmount();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
