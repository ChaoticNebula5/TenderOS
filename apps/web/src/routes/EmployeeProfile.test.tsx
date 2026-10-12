import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as apiClient from "../api/client";
import type { EmployeeResponse } from "../api/client";
import { EmployeeProfile } from "./EmployeeProfile";

// Restyle batch 6. Pins what full-flow.spec.ts :495-498 reads: the
// "Eligible for bid" checkbox and the one "Verify" button exist only once
// the profile has loaded, and after Verify exactly one element inside a
// <dl> reads exactly "yes". Every fixture value is fake.

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>();
  return {
    ...actual,
    getEmployee: vi.fn(),
    patchEmployeeProfile: vi.fn(),
    verifyEmployee: vi.fn(),
    replaceEmployeeResume: vi.fn(),
  };
});

const getEmployee = vi.mocked(apiClient.getEmployee);
const verifyEmployee = vi.mocked(apiClient.verifyEmployee);
const patchEmployeeProfile = vi.mocked(apiClient.patchEmployeeProfile);

function employee(overrides: Partial<EmployeeResponse> = {}): EmployeeResponse {
  return {
    knowledge_item_id: "k1",
    organization_id: "org-1",
    employee_code: "EMP-0042",
    name: "Avery Quill",
    email: "avery@example.test",
    current_title: "Logistics Programme Manager",
    years_experience: 7.5,
    professional_location: "Pune",
    availability_status: "partially_available",
    availability_as_of: "2026-10-01",
    eligible_for_bid: false,
    profile_review_status: "needs_review",
    profile_last_verified_at: null,
    profile_verified_by: null,
    resume_document_id: "d1",
    ...overrides,
  };
}

beforeEach(() => {
  getEmployee.mockResolvedValue(employee());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/talent/employees/k1"]}>
      <Routes>
        <Route path="/talent/employees/:knowledgeItemId" element={<EmployeeProfile />} />
      </Routes>
    </MemoryRouter>,
  );
}

// The deepest elements inside any <dl> whose text is exactly `text` - an
// element matches when its text does and no child's text also does, the
// element :498's dl getByText("yes", {exact: true}) lands on. (The badge's
// <span> holds an icon <svg> child with no text, so it is not a leaf.)
function dlLeavesReading(container: HTMLElement, text: string): Element[] {
  return Array.from(container.querySelectorAll("dl *")).filter(
    (el) =>
      el.textContent === text &&
      !Array.from(el.children).some((child) => child.textContent === text),
  );
}

describe("EmployeeProfile (restyle batch 6)", () => {
  it("renders no checkbox and no Verify button until the profile has loaded (full-flow.spec.ts:496)", async () => {
    let resolve!: (e: EmployeeResponse) => void;
    getEmployee.mockReturnValue(new Promise((r) => (resolve = r)));
    const { container } = renderScreen();

    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /verify/i })).toBeNull();
    expect(container.querySelector("main")!.getAttribute("aria-busy")).toBe("true");

    resolve(employee());
    expect(await screen.findByRole("checkbox", { name: "Eligible for bid" })).not.toBeChecked();
  });

  it("has exactly one button whose name contains 'verify' (full-flow.spec.ts:497)", async () => {
    renderScreen();
    await screen.findByRole("heading", { name: "Avery Quill" });
    expect(screen.getAllByRole("button", { name: /verify/i })).toHaveLength(1);
  });

  it("reads exactly one 'yes' inside a <dl> only after Verify, drawn as a badge (full-flow.spec.ts:498)", async () => {
    const user = userEvent.setup();
    verifyEmployee.mockResolvedValue(
      employee({
        eligible_for_bid: true,
        profile_review_status: "verified",
        profile_last_verified_at: "2026-10-12T06:30:00Z",
      }),
    );
    const { container } = renderScreen();
    await screen.findByRole("heading", { name: "Avery Quill" });

    expect(dlLeavesReading(container, "yes")).toHaveLength(0);
    expect(dlLeavesReading(container, "no")).toHaveLength(1);

    await user.click(screen.getByRole("checkbox", { name: "Eligible for bid" }));
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(verifyEmployee).toHaveBeenCalledWith("k1", true);

    await waitFor(() => expect(dlLeavesReading(container, "yes")).toHaveLength(1));
    const badge = dlLeavesReading(container, "yes")[0];
    expect(badge.classList.contains("status-badge--ready")).toBe(true);
    expect(badge.closest(".badge-raw-word")).not.toBeNull();
    // No other <dl> value reads exactly "yes".
    const ddTexts = Array.from(container.querySelectorAll("dl dd")).map((dd) => dd.textContent);
    expect(ddTexts.filter((t) => t === "yes")).toHaveLength(1);
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("shows every profile field, Not set for nulls, years and dates in words, badges for states", async () => {
    getEmployee.mockResolvedValue(
      employee({
        email: null,
        employee_code: null,
        professional_location: null,
        years_experience: 1,
        availability_status: "unavailable",
        profile_last_verified_at: "2026-10-12T06:30:00Z",
      }),
    );
    const { container } = renderScreen();
    await screen.findByRole("heading", { name: "Avery Quill" });

    const rows = Object.fromEntries(
      Array.from(container.querySelectorAll("dl > div")).map((d) => [
        d.querySelector("dt")!.textContent,
        d.querySelector("dd")!.textContent,
      ]),
    );
    expect(rows).toMatchObject({
      Email: "Not set",
      "Employee code": "Not set",
      "Current title": "Logistics Programme Manager",
      Location: "Not set",
      Experience: "1 year",
      Availability: "Unavailable",
      "Availability as of": "1 Oct 2026",
      "Review status": "Needs review",
      "Bid eligible": "no",
    });
    expect(rows["Last verified"]).toMatch(/^12 Oct 2026, /);
    expect(container.querySelector(".status-badge--blocked")).toHaveTextContent("Unavailable");
    expect(container.textContent).not.toMatch(/partially_available|needs_review|unavailable\b/);
  });

  it("reads 7.5 years and Not set for a never-verified profile", async () => {
    const { container } = renderScreen();
    await screen.findByText("7.5 years");
    const lastVerified = Array.from(container.querySelectorAll("dl > div")).find(
      (d) => d.querySelector("dt")!.textContent === "Last verified",
    )!;
    expect(lastVerified.querySelector("dd")).toHaveTextContent("Not set");
  });

  it("corrects the title through a labelled field and a secondary Save", async () => {
    const user = userEvent.setup();
    patchEmployeeProfile.mockResolvedValue(employee({ current_title: "Programme Director" }));
    renderScreen();
    const input = await screen.findByLabelText("Correct title");
    await user.clear(input);
    await user.type(input, "Programme Director");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(patchEmployeeProfile).toHaveBeenCalledWith("k1", {
      current_title: "Programme Director",
    });
    expect(await screen.findAllByText("Programme Director")).not.toHaveLength(0);
  });

  it("labels the resume input by whether a resume is on file", async () => {
    renderScreen();
    expect(await screen.findByLabelText("Replace resume")).toBeInTheDocument();
    cleanup();
    getEmployee.mockResolvedValue(employee({ resume_document_id: null }));
    renderScreen();
    expect(await screen.findByLabelText("Upload a resume")).toBeInTheDocument();
  });

  it("shows a failed load as a notice under a page title", async () => {
    getEmployee.mockRejectedValue(new Error("down"));
    renderScreen();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Failed to load this employee's profile",
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Employee profile");
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
