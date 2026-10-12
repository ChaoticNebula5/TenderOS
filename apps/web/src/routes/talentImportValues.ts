import { formatDay } from "../format/date";
import { humanize } from "../format/humanize";
import { formatPercent } from "../format/percent";

// The talent import screen's words for backend values (restyle batch 6),
// kept out of the component so each rule is unit-tested on its own - the
// same split as graphExplorerValues.ts. Vocabulary and payload shapes are
// the backend's; paths under packages/tenderos_backend/src/tenderos_backend/
// unless they say apps/. Every label below is chosen, not in DESIGN.

// A null field (DESIGN.md Copy rules: say what is missing, never "-" for a
// known field). The graph explorer uses the same words.
export const NOT_SET = "Not set";

// Import batch type - db/models/knowledge.py:27-29.
const IMPORT_TYPE_LABELS: Readonly<Record<string, string>> = {
  bulk_resumes: "Resumes",
  employee_master_csv: "Employee master (CSV)",
  employee_master_xlsx: "Employee master (XLSX)",
  certification_documents: "Certifications",
  // "manual" is written only by the capability statement upload
  // (apps/api/.../routes/knowledge.py:222), and the worker imports it as
  // exactly that (apps/worker/.../jobs/knowledge_import.py:138).
  manual: "Capability statement",
};

export function importTypeLabel(importType: string): string {
  return Object.hasOwn(IMPORT_TYPE_LABELS, importType)
    ? IMPORT_TYPE_LABELS[importType]
    : humanize(importType);
}

// An import item's source_ref, which is one of:
// - "row_N": an employee master or certification row, N being the real
//   sheet row (the header is row 1, agents/knowledge/employee_master.py:91);
// - "project_N" / "capability_N": the Nth item a capability statement
//   produced, counted from 0 (apps/worker/.../knowledge_import.py:172,
//   :178) - shown counted from 1, as an ordinal, not a sheet position;
// - "_batch_parse_failure": the sentinel for a file that could not be read
//   at all (domain/services/knowledge_service.py:868);
// - otherwise a résumé's file name (knowledge_import.py:245), shown as is.
export function sourceRefLabel(sourceRef: string): string {
  if (sourceRef === "_batch_parse_failure") return "Whole file";
  const row = /^row_(\d+)$/.exec(sourceRef);
  if (row) return `Row ${Number(row[1])}`;
  const ordinal = /^(project|capability)_(\d+)$/.exec(sourceRef);
  if (ordinal) return `${humanize(ordinal[1])} ${Number(ordinal[2]) + 1}`;
  return sourceRef;
}

// Years of experience (a Numeric column, a float in JSON): "1 year",
// "7.5 years". At most two decimals, as the column has no fixed scale.
const YEARS = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

export function formatYears(years: number): string {
  return `${YEARS.format(years)} ${years === 1 ? "year" : "years"}`;
}

export interface PayloadField {
  key: string;
  label: string;
  value: string;
  // "id": a raw identifier (ink-3, may break anywhere); "code": the JSON of
  // a value this file has no words for, shown in <code> rather than hidden.
  kind: "text" | "id" | "code";
}

// The extracted_payload keys of the five payload shapes, in display order:
// résumé (agents/schemas/knowledge.py:27-35), employee master row
// (domain/services/knowledge_service.py:340-344), certification row
// (:745-752), past project (:638-645), capability (:685-690). The payload
// is JSONB, which does not keep key order, so the order is set here.
const FIELD_LABELS: ReadonlyArray<readonly [string, string]> = [
  ["name", "Name"],
  ["employee_code", "Employee code"],
  ["email", "Email"],
  ["current_title", "Current title"],
  ["role", "Role"],
  ["professional_location", "Location"],
  ["years_experience", "Experience"],
  // Free text from the employee master sheet, not a backend enum.
  ["status", "Status in file"],
  ["cert_key", "Certificate key"],
  ["employee_ref", "Employee reference"],
  ["issuer", "Issuer"],
  ["issue_date", "Issued"],
  ["expiry_date", "Expires"],
  ["client", "Client"],
  ["year", "Year"],
  ["description", "Description"],
  ["certification_mentions", "Certifications mentioned"],
  ["project_mentions", "Projects mentioned"],
  ["source_ref", "Reference"],
  // The extraction model's own score (agents/schemas/knowledge.py:35) -
  // not a verification; verification is a person's action.
  ["confidence", "Extraction confidence"],
  ["chunk_id", "Source chunk"],
];
const LABEL_BY_KEY = new Map(FIELD_LABELS);
const ORDER_BY_KEY = new Map(FIELD_LABELS.map(([key], i) => [key, i]));

// A date the backend wrote as an ISO day (certification rows) or the model
// wrote as free text (a résumé's certification mention): formatted when it
// is a real day, otherwise shown as written.
function dayOrText(value: string): string {
  const day = formatDay(value);
  return day === "-" ? value : day;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

// "PMP (PMI, expires 31 Mar 2027)" - agents/schemas/knowledge.py:4-15.
function certificationMention(mention: Record<string, unknown>): string | null {
  const name = text(mention.name);
  if (name === null) return null;
  const issuer = text(mention.issuer);
  const expiry = text(mention.expiry_date);
  const details = [issuer, expiry === null ? null : `expires ${dayOrText(expiry)}`].filter(
    (part): part is string => part !== null,
  );
  return details.length > 0 ? `${name} (${details.join(", ")})` : name;
}

// "Harbour Systems (Example Port Authority, 2023)" - agents/schemas/knowledge.py:18-24.
function projectMention(mention: Record<string, unknown>): string | null {
  const name = text(mention.name);
  if (name === null) return null;
  const details = [text(mention.client), text(mention.year)].filter(
    (part): part is string => part !== null,
  );
  return details.length > 0 ? `${name} (${details.join(", ")})` : name;
}

const MENTION_FORMATTERS: Readonly<
  Record<string, (mention: Record<string, unknown>) => string | null>
> = {
  certification_mentions: certificationMention,
  project_mentions: projectMention,
};

function fieldValue(key: string, value: unknown): Pick<PayloadField, "value" | "kind"> {
  if (value === null || value === undefined || value === "") {
    return { value: NOT_SET, kind: "text" };
  }
  if (key === "chunk_id" && typeof value === "string") return { value, kind: "id" };
  if (key === "confidence" && typeof value === "number") {
    return { value: formatPercent(value) ?? String(value), kind: "text" };
  }
  if (key === "years_experience" && typeof value === "number") {
    return { value: formatYears(value), kind: "text" };
  }
  if ((key === "issue_date" || key === "expiry_date") && typeof value === "string") {
    return { value: dayOrText(value), kind: "text" };
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return { value: "None", kind: "text" };
    const formatter = Object.hasOwn(MENTION_FORMATTERS, key) ? MENTION_FORMATTERS[key] : null;
    if (formatter !== null && value.every(isRecord)) {
      const parts = value.map(formatter);
      if (parts.every((part): part is string => part !== null)) {
        return { value: parts.join("; "), kind: "text" };
      }
    }
    if (value.every((v) => typeof v === "string" || typeof v === "number")) {
      return { value: value.join(", "), kind: "text" };
    }
    return { value: JSON.stringify(value), kind: "code" };
  }
  if (typeof value === "boolean") return { value: value ? "Yes" : "No", kind: "text" };
  if (typeof value === "string" || typeof value === "number") {
    return { value: String(value), kind: "text" };
  }
  // An object this file has no words for: its JSON, in <code>, so nothing
  // the reviewer saw before is hidden and "[object Object]" never shows.
  return { value: JSON.stringify(value), kind: "code" };
}

// The parsed payload as labelled fields, replacing the JSON.stringify dump
// (DESIGN.md principle 4). Known keys in FIELD_LABELS order, then any key
// the backend adds later, alphabetically and humanised. An empty payload
// (the parse-failure sentinel, knowledge_service.py:899) gives no fields.
export function payloadFields(payload: Record<string, unknown>): PayloadField[] {
  const keys = Object.keys(payload).sort((a, b) => {
    const ia = ORDER_BY_KEY.get(a) ?? Number.POSITIVE_INFINITY;
    const ib = ORDER_BY_KEY.get(b) ?? Number.POSITIVE_INFINITY;
    return ia !== ib ? ia - ib : a.localeCompare(b);
  });
  return keys.map((key) => ({
    key,
    label: LABEL_BY_KEY.get(key) ?? humanize(key),
    ...fieldValue(key, payload[key]),
  }));
}
