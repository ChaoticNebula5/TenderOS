import { describe, expect, it } from "vitest";
import { formatPercent } from "./percent";

describe("formatPercent", () => {
  it.each([
    [0.8999999761581421, "90%"],
    [0.5, "50%"],
    [0.125, "13%"],
    [1, "100%"],
    [0, "0%"],
  ])("%s -> %s", (input, expected) => {
    expect(formatPercent(input)).toBe(expected);
  });

  it("never shows 100% for a value below 1", () => {
    expect(formatPercent(0.996)).toBe("99%");
    expect(formatPercent(0.9999)).toBe("99%");
    expect(formatPercent(0.995)).toBe("99%");
  });

  it("never shows 0% for a value above 0", () => {
    expect(formatPercent(0.004)).toBe("1%");
    expect(formatPercent(0.0001)).toBe("1%");
  });

  it("returns null for no value, so the caller chooses the words", () => {
    expect(formatPercent(null)).toBeNull();
    expect(formatPercent(Number.NaN)).toBeNull();
  });
});
