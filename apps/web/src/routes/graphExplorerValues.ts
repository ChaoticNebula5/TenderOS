import { formatDate, formatDay } from "../format/date";
import { humanize } from "../format/humanize";
import { formatPercent } from "../format/percent";
import { describeStatus } from "../status/statusMap";
import type { StatusFamily } from "../status/statusMap";

// The graph explorer's value formatting (GraphExplorer.tsx), kept beside the
// screen it serves; pure functions, so they are unit-tested on their own.
//
// Graph explorer values as text, never JSON.stringify output to an operator
// (DESIGN.md Do's and Don'ts) - except where a value has no flat text form.
//
// chosen, not in DESIGN (batch 5): Neo4j node properties are flat values
// in practice (strings, numbers, booleans, arrays of those), so:
// - text and numbers show as text, booleans as "Yes"/"No", null as "Not set";
// - an array of plain values is joined with ", ";
// - anything else (an object, or an array holding objects) has no honest
//   flat form, so it falls back to its JSON text, which the caller renders
//   in a <code> element - never "[object Object]".

export type GraphValue = { kind: "text"; text: string } | { kind: "json"; text: string };

function isPlain(value: unknown): value is string | number | boolean | null {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function plainText(value: string | number | boolean | null): string {
  if (value === null) return "Not set";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

export function graphValue(value: unknown): GraphValue {
  if (value === undefined) return { kind: "text", text: "Not set" };
  if (isPlain(value)) return { kind: "text", text: plainText(value) };
  if (Array.isArray(value) && value.every(isPlain)) {
    return { kind: "text", text: value.map(plainText).join(", ") };
  }
  return { kind: "json", text: JSON.stringify(value) };
}

// The property most likely to name a node for a person, in order; then a
// derived name (below); the id last. chosen, not in DESIGN. "" when the node carries none of them.
// status and finding_category are backend words, so they are humanised
// (No Raw Enum Rule): "fully_compliant" -> "Fully compliant".
const SUMMARY_KEYS = [
  "display_code",
  "name",
  "title",
  "section_name",
  "role_name",
  "finding_category",
  "status",
];

// Two labels the projector writes with no name-like property at all
// (graph_projection/projector.py): ProposalClaim (:376-381 - no claim_text
// is projected) and Evidence (:319-324 - no title or excerpt). Rather than
// fall through to a raw UUID, name them from what they do carry:
// "Capability claim", "Past project evidence". chosen, not in DESIGN.
const DERIVED_NAMES: ReadonlyArray<[key: string, suffix: string]> = [
  ["claim_type", "claim"],
  ["source_kind", "evidence"],
];

export function nodeSummary(node: Record<string, unknown> | null | undefined): string {
  if (!node) return "";
  for (const key of SUMMARY_KEYS) {
    const value = node[key];
    if (typeof value === "string" && value.length > 0) {
      return key === "status" || key === "finding_category" ? humanize(value) : value;
    }
    if (typeof value === "number") return String(value);
  }
  for (const [key, suffix] of DERIVED_NAMES) {
    const value = node[key];
    if (typeof value === "string" && value.length > 0) return `${humanize(value)} ${suffix}`;
  }
  return typeof node.id === "string" ? node.id : "";
}

// "Requirement STAFF-004" style summary of every node a contradiction row
// names, keyed by the row's own field names ("evidence", "target", ...).
export function contradictionParts(
  row: Record<string, unknown>,
): Array<{ key: string; text: string }> {
  return Object.entries(row)
    .filter(([key]) => key !== "category")
    .map(([key, value]) => {
      if (value !== null && typeof value === "object" && !Array.isArray(value)) {
        const summary = nodeSummary(value as Record<string, unknown>);
        return { key, text: summary || graphValue(value).text };
      }
      // "via" is a relationship type ("PROVIDES"), humanised as in the
      // neighbours table.
      if (key === "via" && typeof value === "string") return { key, text: humanize(value) };
      return { key, text: graphValue(value).text };
    });
}

// ---------------------------------------------------------------------------
// Polish pass (P6): node properties in words. The keys are the ones
// graph_projection/projector.py actually writes (measured: status,
// validity_status, grounding_status, availability_status,
// profile_review_status, valid_from/valid_until/submission_date/start_date/
// end_date, selected_at/deselected_at, confidence, and id plus several
// *_id keys); before this, they showed as the backend wrote them
// ("fully_compliant", "2026-12-31", "0.8999999761581421"). chosen, not in
// DESIGN.

export type PropertyValue = GraphValue | { kind: "id"; text: string };

// Which status family a status-like key belongs to. A bare `status` depends
// on the node: Compliance nodes carry compliance values, knowledge items
// their own, proposal sections theirs (projector.py).
const STATUS_KEY_FAMILY: Readonly<Record<string, StatusFamily>> = {
  validity_status: "validity",
  grounding_status: "grounding",
  availability_status: "availability",
  profile_review_status: "profileReview",
};
const STATUS_BY_LABEL: Readonly<Record<string, StatusFamily>> = {
  Compliance: "compliance",
  ProposalSection: "proposalSection",
  Employee: "knowledgeItem",
  Certification: "knowledgeItem",
  Project: "knowledgeItem",
  Capability: "knowledgeItem",
};

function statusFamily(key: string, labels: readonly string[]): StatusFamily | null {
  if (Object.hasOwn(STATUS_KEY_FAMILY, key)) return STATUS_KEY_FAMILY[key];
  if (key !== "status") return null;
  for (const label of labels) {
    if (Object.hasOwn(STATUS_BY_LABEL, label)) return STATUS_BY_LABEL[label];
  }
  return null;
}

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

export function isIdKey(key: string): boolean {
  return key === "id" || key.endsWith("_id");
}

// One property's value in words. Ids stay visible (nothing that was shown is
// hidden) but are marked "id", for the screen to show quietly and last.
export function propertyValue(
  key: string,
  value: unknown,
  labels: readonly string[] = [],
): PropertyValue {
  if (typeof value === "string" && isIdKey(key)) return { kind: "id", text: value };
  if (typeof value === "string" && value.length > 0) {
    const family = statusFamily(key, labels);
    if (family !== null) {
      const { label } = describeStatus(family, value);
      // describeStatus falls back to the raw value for one it does not know.
      return { kind: "text", text: label === value ? humanize(value) : label };
    }
    if (key.endsWith("_status") || key === "status") return { kind: "text", text: humanize(value) };
    if (DATE_ONLY.test(value)) return { kind: "text", text: formatDay(value) };
    if (key.endsWith("_at")) {
      const when = formatDate(value);
      return { kind: "text", text: when === "-" ? value : when };
    }
  }
  if (key === "confidence" && typeof value === "number") {
    return { kind: "text", text: formatPercent(value) ?? String(value) };
  }
  return graphValue(value);
}

// The node's properties in display order: everything else first, in the
// order the node gives them, then the ids.
export function propertyEntries(node: Record<string, unknown>): Array<[string, unknown]> {
  const entries = Object.entries(node);
  return [...entries.filter(([k]) => !isIdKey(k)), ...entries.filter(([k]) => isIdKey(k))];
}
