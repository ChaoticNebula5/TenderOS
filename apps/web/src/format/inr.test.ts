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
