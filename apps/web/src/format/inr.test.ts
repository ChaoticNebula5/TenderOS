import { describe, expect, it } from "vitest";
import { CRORE, LAKH, formatInr, pickCompactUnit } from "./inr";

// All amounts here are fake test values, not data from any tender.

describe("formatInr full mode", () => {
  it.each([
    [0, "₹0"],
    [99999, "₹99,999"],
    [100000, "₹1,00,000"],
    [4500000, "₹45,00,000"],
    [9999950, "₹99,99,950"],
    [9999999, "₹99,99,999"],
    [10000000, "₹1,00,00,000"],
    [12000000, "₹1,20,00,000"],
    [-4500000, "-₹45,00,000"],
  ])("%d -> %s", (value, expected) => {
    expect(formatInr(value, "full")).toBe(expected);
  });

  it("shows exactly two decimals for a non-whole amount", () => {
    expect(formatInr(1234.5, "full")).toBe("₹1,234.50");
    expect(formatInr(1234.567, "full")).toBe("₹1,234.57");
  });

  it("drops decimals when rounding to paise makes the amount whole", () => {
    expect(formatInr(1234.999, "full")).toBe("₹1,235");
  });

  it("does not show a minus sign on an amount that rounds to zero", () => {
    expect(formatInr(-0.001, "full")).toBe("₹0");
  });
});

describe("formatInr compact mode", () => {
  it.each([
    [0, "₹0"],
    [99999, "₹99,999"],
    [100000, "₹1.0 L"],
    [4500000, "₹45.0 L"],
    [9999950, "₹1.00 Cr"],
    [9999999, "₹1.00 Cr"],
    [10000000, "₹1.00 Cr"],
    [12000000, "₹1.20 Cr"],
    [-4500000, "-₹45.0 L"],
  ])("%d -> %s", (value, expected) => {
    expect(formatInr(value, "compact")).toBe(expected);
  });
});

describe("formatInr missing or non-finite input", () => {
  it.each([null, undefined, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    "%s -> -",
    (value) => {
      expect(formatInr(value, "full")).toBe("-");
      expect(formatInr(value, "compact")).toBe("-");
    },
  );
});

describe("pickCompactUnit", () => {
  const lakhs = { divisor: LAKH, suffix: "L", decimals: 1 };
  const crores = { divisor: CRORE, suffix: "Cr", decimals: 2 };

  it.each([
    [100000, lakhs],
    [4500000, lakhs],
    // 99.94999 lakhs rounds to 99.9 - still lakhs.
    [9994999, lakhs],
    // 99.9995 lakhs rounds to 100.0 - promoted although below a crore.
    [9999950, crores],
    [9999999, crores],
    [10000000, crores],
    [12000000, crores],
  ])("%d", (rupees, expected) => {
    expect(pickCompactUnit(rupees)).toEqual(expected);
  });
});

// Sweeps across the lakh-to-crore boundary (fake values). They guard the
// promotion rule as a property, not just at the hand-picked points above.
describe("formatInr compact sweeps across the crore boundary", () => {
  // Number shown before the unit, sign and grouping removed.
  function shownNumber(output: string): number {
    return Number(
      output
        .replace(/^-?₹/, "")
        .replace(/ (L|Cr)$/, "")
        .replace(/,/g, ""),
    );
  }

  // Every value must show a unit, never "100.0 L", lakhs below 100.0 and
  // crores at least 1.00. Returns the values that break any of those.
  function sweepFailures(values: number[]): string[] {
    const failures: string[] = [];
    for (const value of values) {
      const output = formatInr(value, "compact");
      if (output.includes("100.0 L")) failures.push(`${value} -> ${output} (100.0 L)`);
      if (output.endsWith(" L") && !(shownNumber(output) < 100)) {
        failures.push(`${value} -> ${output} (L not below 100.0)`);
      }
      if (output.endsWith(" Cr") && !(shownNumber(output) >= 1)) {
        failures.push(`${value} -> ${output} (Cr below 1.00)`);
      }
      if (!output.endsWith(" L") && !output.endsWith(" Cr")) {
        failures.push(`${value} -> ${output} (no unit)`);
      }
    }
    return failures;
  }

  // Every 50 rupees from 99,90,000 to 1,00,10,000, plus negatives near it.
  const wholeRupees: number[] = [];
  for (let rupees = 9_990_000; rupees <= 10_010_000; rupees += 50) wholeRupees.push(rupees);
  wholeRupees.push(-9_994_999, -9_995_000, -9_999_950, -9_999_999, -10_000_000, -10_000_050);

  it(`whole rupees: checks ${wholeRupees.length} values`, () => {
    expect(sweepFailures(wholeRupees)).toEqual([]);
  });

  // Every 0.25 rupee from 99,99,000 to 1,00,00,100. Built from an integer
  // index so each value is exact (0.25 is exact in binary), not an
  // accumulated float sum.
  const quarterRupees: number[] = [];
  for (let i = 0; i <= (10_000_100 - 9_999_000) * 4; i++) quarterRupees.push(9_999_000 + i * 0.25);

  it(`paise: checks ${quarterRupees.length} values`, () => {
    expect(sweepFailures(quarterRupees)).toEqual([]);
  });
});
