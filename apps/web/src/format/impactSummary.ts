import type { GraphImpactRow } from "../api/client";

// The one-line summary above the knowledge-selection impact list
// ("3 affected: Requirement 2, Compliance 1"), from the rows the preview
// response already carries - DESIGN.md Screen patterns, "Impact list", in
// the part the E2E allows: the list itself must stay one raw-JSON <li> per
// row (full-flow.spec.ts:1258 parses each one), so this summary is the
// readable view and sits outside the list.
//
// Rules (decided at batch 4 plan review):
// - total is rows.length - always the number of <li> rendered below it;
// - byLabel counts only rows whose shape is recognised: a `labels` array
//   holding at least one string. A row with several labels counts once per
//   distinct label, so the breakdown can sum to more than total;
// - an unrecognised row counts in total but not in byLabel.
// Ordered by count, highest first, then by label, so the output is stable.
// The caller renders nothing when total is 0: the empty response already
// has its own sentence (full-flow.spec.ts:1254).

export interface ImpactSummary {
  total: number;
  byLabel: Array<{ label: string; count: number }>;
}

function recognisedLabels(row: GraphImpactRow): string[] {
  const labels = row.labels;
  if (!Array.isArray(labels)) return [];
  const strings = labels.filter((l): l is string => typeof l === "string" && l.length > 0);
  return [...new Set(strings)];
}

export function summarizeImpact(rows: GraphImpactRow[]): ImpactSummary {
  const counts = new Map<string, number>();
  for (const row of rows) {
    for (const label of recognisedLabels(row)) {
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
  }
  const byLabel = [...counts]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  return { total: rows.length, byLabel };
}

// Neo4j label to words: "ProposalClaim" -> "Proposal claim". chosen, not in
// DESIGN: the labels are graph node types, not a backend enum in the label
// map, so they are re-cased only, never renamed.
export function labelWords(label: string): string {
  const words = label.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
