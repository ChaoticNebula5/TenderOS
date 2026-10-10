import { describe, expect, it } from "vitest";
import { formatDate } from "./date";

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
