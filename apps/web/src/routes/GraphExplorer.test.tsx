import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { GraphTraceResponse } from "../api/client";
import { StatusIcon } from "../components/ui/icons";
import { GraphExplorer } from "./GraphExplorer";

// Restyle batch 5. The spec never visits this screen; these pin the
// restyle's own promises: no JSON.stringify output except the <code>
// fallback, no "[object Object]", the Fresh/Stale badge, Explore. Fake data.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    getGraphTrace: vi.fn(),
    getGraphStatus: vi.fn(),
    getGraphContradictions: vi.fn(),
    triggerGraphProjection: vi.fn(),
  };
});

const getGraphTrace = vi.mocked(apiClient.getGraphTrace);
const getGraphStatus = vi.mocked(apiClient.getGraphStatus);
const getGraphContradictions = vi.mocked(apiClient.getGraphContradictions);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function trace(overrides: Partial<GraphTraceResponse> = {}): GraphTraceResponse {
  return {
    n: {
      id: "r-1",
      display_code: "STAFF-004",
      tender_id: "t1",
      tags: ["mandatory", "staffing"],
      extra: { source: "fixture", page: 3 },
    },
    node_labels: ["Requirement"],
    neighbors: [
      {
        relationship: "SATISFIES",
        neighbor: { id: "c-1", status: "fully_compliant" },
        neighbor_labels: ["Compliance"],
      },
    ],
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/tenders/t1/graph"]}>
      <Routes>
        <Route path="/tenders/:tenderId/graph" element={<GraphExplorer />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function load(user: ReturnType<typeof userEvent.setup>, id = "r-1") {
  await user.selectOptions(screen.getByRole("combobox", { name: "Node type" }), "Requirement");
  await user.type(screen.getByRole("textbox", { name: "Node id" }), id);
  await user.click(screen.getByRole("button", { name: "Load" }));
}

describe("GraphExplorer (restyle batch 5)", () => {
  it("shows an empty state before any node is loaded, and labelled look-up fields", () => {
    renderScreen();
    expect(screen.getByText("No node loaded yet")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Node type" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Node id" })).toBeInTheDocument();
  });

  it("renders properties as text, joins arrays, puts objects in <code>, and never shows '[object Object]'", async () => {
    const user = userEvent.setup();
    getGraphTrace.mockResolvedValue(trace());
    getGraphStatus.mockResolvedValue({
      fresh: false,
      current_state_version: 13,
      projected_state_version: 12,
      last_projected_at: "2026-10-10T08:00:00Z",
    });
    getGraphContradictions.mockResolvedValue([]);
    const { container } = renderScreen();

    await load(user);
    await screen.findByRole("heading", { name: "Requirement" });
    expect(screen.getByText("STAFF-004")).toBeInTheDocument();
    expect(screen.getByText("mandatory, staffing")).toBeInTheDocument();
    const code = container.querySelector(".graph__props code")!;
    expect(code.textContent).toBe('{"source":"fixture","page":3}');
    expect(container.textContent).not.toContain("[object Object]");
    // The only JSON on the page is that <code> fallback.
    const textOutsideCode = Array.from(container.querySelectorAll("main *"))
      .filter((el) => el.children.length === 0 && el.tagName !== "CODE")
      .map((el) => el.textContent ?? "");
    expect(textOutsideCode.some((t) => t.startsWith("{"))).toBe(false);
    expect(await screen.findByText("Stale")).toBeInTheDocument();
  });

  it("lists neighbours in a table and Explore loads the neighbour", async () => {
    const user = userEvent.setup();
    getGraphTrace.mockResolvedValueOnce(trace()).mockResolvedValueOnce(
      trace({
        n: { id: "c-1", status: "fully_compliant" },
        node_labels: ["Compliance"],
        neighbors: [],
      }),
    );
    getGraphStatus.mockResolvedValue({
      fresh: true,
      current_state_version: 13,
      projected_state_version: 13,
      last_projected_at: null,
    });
    getGraphContradictions.mockResolvedValue([]);
    renderScreen();

    await load(user);
    const table = await screen.findByRole("table");
    const row = within(table).getAllByRole("row")[1];
    expect(within(row).getByText("Satisfies")).toBeInTheDocument();
    expect(within(row).getByText("Compliance")).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Explore" }));
    await waitFor(() => expect(getGraphTrace).toHaveBeenLastCalledWith("t1", "Compliance", "c-1"));
    expect(await screen.findByText("No neighbors.")).toBeInTheDocument();
    expect(screen.getByText("Fresh")).toBeInTheDocument();
  });

  it("names contradictions instead of printing their JSON", async () => {
    const user = userEvent.setup();
    getGraphTrace.mockResolvedValue(trace());
    getGraphStatus.mockResolvedValue({
      fresh: true,
      current_state_version: 1,
      projected_state_version: 1,
      last_projected_at: null,
    });
    getGraphContradictions.mockResolvedValue([
      { category: "deselected_fact_in_use", fact: { id: "k1", name: "Dana Whitfield" } },
    ]);
    renderScreen();

    await load(user);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("1 graph contradiction");
    expect(within(alert).getByText("Deselected fact in use")).toBeInTheDocument();
    expect(within(alert).getByText("Fact: Dana Whitfield")).toBeInTheDocument();
    expect(alert.textContent).not.toContain("{");
    // Polish pass (decided): the failed tone - the red team's critical defect.
    // The icon is the failed one (same drawing as StatusIcon tone="failed").
    const { container: failedIcon } = render(<StatusIcon tone="failed" />);
    expect(alert.querySelector("h2 svg")!.innerHTML).toBe(
      failedIcon.querySelector("svg")!.innerHTML,
    );
  });

  // Polish pass (P6): a Compliance node with the projector's own keys
  // (status, human_approved, confidence) reads in words; ids come last.
  it("shows a node's properties in words, ids last and quiet", async () => {
    const user = userEvent.setup();
    getGraphTrace.mockResolvedValue(
      trace({
        n: {
          id: "c-1",
          tender_id: "t1",
          status: "partially_compliant",
          human_approved: false,
          confidence: 0.8999999761581421,
        },
        node_labels: ["Compliance"],
        neighbors: [],
      }),
    );
    getGraphStatus.mockResolvedValue({
      fresh: true,
      current_state_version: 1,
      projected_state_version: 1,
      last_projected_at: null,
    });
    getGraphContradictions.mockResolvedValue([]);
    const { container } = renderScreen();

    await load(user, "c-1");
    await screen.findByRole("heading", { name: "Compliance" });
    const rows = Array.from(container.querySelectorAll(".graph__props > div")).map((d) => [
      d.querySelector("dt")!.textContent,
      d.querySelector("dd")!.textContent,
    ]);
    expect(rows).toEqual([
      ["Status", "Partly compliant"],
      ["Human approved", "No"],
      ["Confidence", "90%"],
      ["Id", "c-1"],
      ["Tender id", "t1"],
    ]);
    expect(container.querySelectorAll(".graph__props dd.graph__id")).toHaveLength(2);
    expect(container.textContent).not.toContain("partially_compliant");
  });
});
