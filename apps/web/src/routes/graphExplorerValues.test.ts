import { describe, expect, it } from "vitest";
import {
  contradictionParts,
  graphValue,
  nodeSummary,
  propertyEntries,
  propertyValue,
} from "./graphExplorerValues";

describe("graphValue", () => {
  it.each([
    ["STAFF-004", { kind: "text", text: "STAFF-004" }],
    [13, { kind: "text", text: "13" }],
    [true, { kind: "text", text: "Yes" }],
    [false, { kind: "text", text: "No" }],
    [null, { kind: "text", text: "Not set" }],
    [undefined, { kind: "text", text: "Not set" }],
  ])("%s as text", (value, expected) => {
    expect(graphValue(value)).toEqual(expected);
  });

  it("joins an array of plain values", () => {
    expect(graphValue(["Requirement", "Compliance", 3])).toEqual({
      kind: "text",
      text: "Requirement, Compliance, 3",
    });
  });

  it("falls back to JSON text for objects and arrays of objects, never '[object Object]'", () => {
    const obj = graphValue({ a: 1, b: "x" });
    expect(obj).toEqual({ kind: "json", text: '{"a":1,"b":"x"}' });
    const nested = graphValue([{ a: 1 }]);
    expect(nested).toEqual({ kind: "json", text: '[{"a":1}]' });
    expect(obj.text).not.toContain("[object Object]");
  });
});

describe("nodeSummary", () => {
  it("prefers a human name over the id", () => {
    expect(nodeSummary({ id: "u-1", display_code: "STAFF-004" })).toBe("STAFF-004");
    expect(nodeSummary({ id: "u-1", title: "Port Expansion 2024" })).toBe("Port Expansion 2024");
    expect(nodeSummary({ id: "u-1" })).toBe("u-1");
    // Backend words are humanised, never shown raw.
    expect(nodeSummary({ id: "c-1", status: "fully_compliant" })).toBe("Fully compliant");
  });

  // Property sets as graph_projection/projector.py writes them (batch 5,
  // check 3): every label gets a name, none falls through to "".
  it.each([
    [
      "Tender",
      {
        id: "t1",
        tender_id: "t1",
        organization_id: "o",
        name: "Port tender",
        status: "created",
        state_version: 3,
      },
      "Port tender",
    ],
    [
      "Requirement",
      {
        id: "r1",
        tender_id: "t1",
        display_code: "STAFF-004",
        category: "staffing",
        mandatory: true,
        verified: true,
      },
      "STAFF-004",
    ],
    [
      "Evidence",
      {
        id: "e1",
        tender_id: "t1",
        source_kind: "past_project",
        verified: true,
        validity_status: "valid",
        confidence: 0.9,
      },
      "Past project evidence",
    ],
    [
      "Compliance",
      {
        id: "c1",
        tender_id: "t1",
        status: "partially_compliant",
        human_approved: false,
        confidence: 0.5,
      },
      "Partially compliant",
    ],
    [
      "Role",
      { id: "ro1", tender_id: "t1", role_name: "Project Manager", min_experience_years: 8 },
      "Project Manager",
    ],
    [
      "ProposalSection",
      { id: "s1", tender_id: "t1", section_name: "Approach", status: "draft", version: 1 },
      "Approach",
    ],
    [
      "ProposalClaim",
      {
        id: "pc1",
        tender_id: "t1",
        claim_type: "capability",
        is_factual: true,
        claim_kind: "current",
        grounding_status: "unsupported",
      },
      "Capability claim",
    ],
    [
      "Employee",
      {
        knowledge_item_id: "k1",
        organization_id: "o",
        status: "approved",
        name: "Dana Whitfield",
        current_title: "PM",
      },
      "Dana Whitfield",
    ],
    [
      "Certification",
      {
        knowledge_item_id: "k2",
        organization_id: "o",
        status: "approved",
        title: "ISO 9001",
        issuer: "BSI",
      },
      "ISO 9001",
    ],
    [
      "Project",
      {
        knowledge_item_id: "k3",
        organization_id: "o",
        status: "approved",
        title: "Port Expansion 2024",
      },
      "Port Expansion 2024",
    ],
    [
      "Capability",
      {
        knowledge_item_id: "k4",
        organization_id: "o",
        status: "approved",
        title: "Cold chain",
        category: "logistics",
      },
      "Cold chain",
    ],
  ])("names a projected %s node", (_label, node, expected) => {
    expect(nodeSummary(node)).toBe(expected);
    expect(nodeSummary({})).toBe("");
    expect(nodeSummary(null)).toBe("");
  });
});

describe("contradictionParts", () => {
  it("summarises each named node and skips the category", () => {
    expect(
      contradictionParts({
        category: "deselected_fact_in_use",
        fact: { id: "k1", name: "Dana Whitfield" },
        via: "PROVIDES",
        target: { id: "c1" },
      }),
    ).toEqual([
      { key: "fact", text: "Dana Whitfield" },
      { key: "via", text: "Provides" },
      { key: "target", text: "c1" },
    ]);
  });
});

// Polish pass (P6): properties in words, with the keys
// graph_projection/projector.py really writes. Fake values.
describe("propertyValue", () => {
  it("names status values through their family, by key or by node label", () => {
    expect(propertyValue("status", "partially_compliant", ["Compliance"])).toEqual({
      kind: "text",
      text: "Partly compliant",
    });
    expect(propertyValue("grounding_status", "needs_review")).toEqual({
      kind: "text",
      text: "Needs review",
    });
    expect(propertyValue("validity_status", "expired")).toEqual({ kind: "text", text: "Expired" });
    expect(propertyValue("availability_status", "partially_available")).toEqual({
      kind: "text",
      text: "Partly available",
    });
    expect(propertyValue("profile_review_status", "verified")).toEqual({
      kind: "text",
      text: "Verified",
    });
    expect(propertyValue("status", "active", ["Employee"])).toEqual({
      kind: "text",
      text: "Active",
    });
  });

  it("humanises a status it has no family for, never shows the raw word", () => {
    expect(propertyValue("status", "some_new_state", ["Requirement"])).toEqual({
      kind: "text",
      text: "Some new state",
    });
    expect(propertyValue("status", "brand_new", ["Compliance"])).toEqual({
      kind: "text",
      text: "Brand new",
    });
  });

  it("formats date-only values, timestamps and confidence", () => {
    expect(propertyValue("valid_until", "2026-12-31")).toEqual({
      kind: "text",
      text: "31 Dec 2026",
    });
    expect(propertyValue("submission_date", "2026-11-30")).toEqual({
      kind: "text",
      text: "30 Nov 2026",
    });
    const at = propertyValue("selected_at", "2026-10-10T08:35:00Z");
    expect(at.kind).toBe("text");
    expect(at.text).toMatch(/^10 Oct 2026, \d{2}:\d{2}$/);
    expect(propertyValue("confidence", 0.8999999761581421)).toEqual({ kind: "text", text: "90%" });
  });

  it("marks ids, keeps other values as graphValue does", () => {
    expect(propertyValue("id", "abc")).toEqual({ kind: "id", text: "abc" });
    expect(propertyValue("tender_id", "t1")).toEqual({ kind: "id", text: "t1" });
    expect(propertyValue("display_code", "STAFF-004")).toEqual({ kind: "text", text: "STAFF-004" });
    expect(propertyValue("human_approved", false)).toEqual({ kind: "text", text: "No" });
    expect(propertyValue("category", "staffing")).toEqual({ kind: "text", text: "staffing" });
  });
});

describe("propertyEntries", () => {
  it("lists ids last, everything else in the node's own order", () => {
    expect(
      propertyEntries({ id: "c1", tender_id: "t1", status: "x", confidence: 0.5 }).map(([k]) => k),
    ).toEqual(["status", "confidence", "id", "tender_id"]);
  });
});
