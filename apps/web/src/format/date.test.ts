import { describe, expect, it } from "vitest";
import { formatDate, formatDay } from "./date";

// One fixed instant (fake), shown in two zones to prove the zone parameter
// is what decides the clock time, not the machine running the test.
const INSTANT = "2026-10-10T08:35:00Z";

describe("formatDate", () => {
  it("formats in Asia/Kolkata", () => {
    expect(formatDate(INSTANT, "Asia/Kolkata")).toBe("10 Oct 2026, 14:05");
  });

  it("formats the same instant in UTC", () => {
    expect(formatDate(INSTANT, "UTC")).toBe("10 Oct 2026, 08:35");
  });

  it("accepts a Date object", () => {
    expect(formatDate(new Date(INSTANT), "UTC")).toBe("10 Oct 2026, 08:35");
  });

  it("uses a 24-hour clock past noon and midnight as 00", () => {
    expect(formatDate("2026-10-10T18:30:00Z", "UTC")).toBe("10 Oct 2026, 18:30");
    expect(formatDate("2026-10-10T00:05:00Z", "UTC")).toBe("10 Oct 2026, 00:05");
  });

  it.each([null, undefined, "", "not a date", "2026-13-45T99:99:00Z"])("%s -> -", (value) => {
    expect(formatDate(value, "UTC")).toBe("-");
  });
});

// Batch 6. Fake dates.
describe("formatDay", () => {
  it("formats a date-only value with no clock time", () => {
    expect(formatDay("2025-03-31")).toBe("31 Mar 2025");
    expect(formatDay("2026-01-01")).toBe("1 Jan 2026");
  });

  // The bug it exists to avoid: formatDate treats "2025-03-31" as midnight
  // UTC, which is still the 30th in a zone west of UTC.
  it("shows the stored day where formatDate would show the day before", () => {
    expect(formatDate("2025-03-31", "America/New_York")).toMatch(/^30 Mar 2025/);
    expect(formatDay("2025-03-31")).toBe("31 Mar 2025");
  });

  it("uses en-IN's month abbreviations", () => {
    expect(formatDay("2026-09-05")).toBe("5 Sept 2026");
  });

  it.each([
    null,
    undefined,
    "",
    "not a date",
    "2025-02-30",
    "2025-13-01",
    "2025-3-31",
    "2025-03-31T00:00:00Z",
  ])("%s -> -", (value) => {
    expect(formatDay(value)).toBe("-");
  });
});
