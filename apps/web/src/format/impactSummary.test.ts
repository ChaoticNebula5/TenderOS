import { describe, expect, it } from "vitest";
import { labelWords, summarizeImpact } from "./impactSummary";

describe("summarizeImpact", () => {
  it("is empty for no rows", () => {
    expect(summarizeImpact([])).toEqual({ total: 0, byLabel: [] });
  });

  it("counts each row's label, highest count first", () => {
    const rows = [
      { labels: ["Requirement"], affected: { id: "r1" } },
      { labels: ["Compliance"], affected: { id: "c1" } },
      { labels: ["Requirement"], affected: { id: "r2" } },
    ];
    expect(summarizeImpact(rows)).toEqual({
      total: 3,
      byLabel: [
        { label: "Requirement", count: 2 },
        { label: "Compliance", count: 1 },
      ],
    });
  });

  it("counts a multi-label row once per distinct label, so the breakdown can exceed the total", () => {
    const rows = [{ labels: ["Requirement", "Compliance", "Requirement"] }];
    expect(summarizeImpact(rows)).toEqual({
      total: 1,
      byLabel: [
        { label: "Compliance", count: 1 },
        { label: "Requirement", count: 1 },
      ],
    });
  });

  it("keeps unrecognised rows in the total but out of the breakdown", () => {
    const rows = [
      { labels: ["Role"] },
      { labels: "Role" },
      { labels: [] },
      { labels: [42, null] },
      { affected: { id: "x" } },
    ];
    expect(summarizeImpact(rows)).toEqual({
      total: 5,
      byLabel: [{ label: "Role", count: 1 }],
    });
  });
});

describe("labelWords", () => {
  it.each([
    ["ProposalClaim", "Proposal claim"],
    ["Requirement", "Requirement"],
    ["ProposalSection", "Proposal section"],
  ])("%s -> %s", (input, expected) => {
    expect(labelWords(input)).toBe(expected);
  });
});
