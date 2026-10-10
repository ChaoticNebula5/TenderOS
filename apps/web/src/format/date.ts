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

export function formatDate(
  value: string | Date | null | undefined,
  timeZone?: string,
): string {
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
