// The one date-time formatter (DESIGN.md Copy rules: "10 Oct 2026, 14:05",
// en-IN, never a raw ISO string).
//
// Timezone: the viewer's local zone by default - Intl uses the browser's
// zone when timeZone is undefined, so an operator in Mumbai sees IST and
// one abroad sees their own clock. `timeZone` is injectable (any IANA name,
// e.g. "Asia/Kolkata", "UTC") so tests are deterministic on any machine.
//
// null, undefined, and anything that does not parse to a real date
// return "-", the screens' existing placeholder for a missing value.

const PLACEHOLDER = "-";

export function formatDate(value: string | Date | null | undefined, timeZone?: string): string {
  if (value === null || value === undefined || value === "") return PLACEHOLDER;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return PLACEHOLDER;
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).format(date);
}

// A calendar date with no time ("2025-03-31" -> "31 Mar 2025"): the
// backend's `date` columns (e.g. knowledge_items.valid_until,
// employees.availability_as_of, a certification row's expiry date).
//
// Not formatDate: `new Date("2025-03-31")` is midnight UTC, so formatting
// it in the viewer's zone shows the 30th anywhere west of UTC, and adds a
// clock time the value never had. This one reads the three numbers itself
// and formats them in UTC, so the day printed is the day stored, in every
// zone. Batch 6, decided at plan review.
//
// Only a strict YYYY-MM-DD that names a real day is accepted ("2025-02-30"
// is rejected, where `new Date` would roll it to 2 March); anything else -
// including a full timestamp - returns "-", like formatDate.
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

export function formatDay(value: string | null | undefined): string {
  if (value === null || value === undefined) return PLACEHOLDER;
  const match = DATE_ONLY.exec(value);
  if (!match) return PLACEHOLDER;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return PLACEHOLDER;
  }
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}
