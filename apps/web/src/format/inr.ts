// The one INR formatter (DESIGN.md Copy rules, decision 5). Every money
// value on screen goes through formatInr; nothing else in src formats money.
//
// Two modes:
// - "full": exact amount with Indian digit grouping, "₹1,20,00,000". Whole
//   amounts show no decimals; anything else shows exactly two (paise).
// - "compact": for tables and cards. Below one lakh it is the full figure;
//   from one lakh it is lakhs to one decimal ("₹45.0 L") or crores to two
//   ("₹1.20 Cr"), with the unit picked AFTER rounding (pickCompactUnit).
//
// null, undefined, NaN and ±Infinity all return "-", the placeholder the
// screens already use for a missing value (BidDashboard.tsx client column).

export type InrMode = "compact" | "full";

export interface CompactUnit {
  divisor: number;
  suffix: "L" | "Cr";
  decimals: number;
}

export const LAKH = 100_000;
export const CRORE = 10_000_000;

const PLACEHOLDER = "-";

function grouped(value: number, decimals: number): string {
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

// Rounds to paise once, so every later decision (whole or not, below a
// lakh or not, negative or zero) is made on the number that is displayed.
function toPaise(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * Picks the compact unit for a rupee amount that is at least one lakh.
 *
 * Contract:
 * - `rupees` is non-negative and >= LAKH (100000). formatInr guarantees
 *   this; amounts below a lakh never reach here.
 * - Returns lakhs ({ divisor: LAKH, suffix: "L", decimals: 1 }) or crores
 *   ({ divisor: CRORE, suffix: "Cr", decimals: 2 }).
 * - Must promote to crores when the amount, rounded to one decimal in
 *   lakhs, would reach 100.0 - so 9999999 is "₹1.00 Cr", never "₹100.0 L".
 */
export function pickCompactUnit(rupees: number): CompactUnit {
  // Decide on the ROUNDED lakhs figure, the one the screen would show.
  // Comparing raw rupees against CRORE is wrong for 99,95,000-99,99,999:
  // below a crore, yet "100.0 L" once rounded to one decimal.
  // Math.round(x * 10) / 10 rounds half up, matching Intl for the
  // non-negative values this receives.
  const roundedLakhs = Math.round((rupees / LAKH) * 10) / 10;
  if (roundedLakhs < 100) {
    return { divisor: LAKH, suffix: "L", decimals: 1 };
  }
  return { divisor: CRORE, suffix: "Cr", decimals: 2 };
}

export function formatInr(value: number | null | undefined, mode: InrMode): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return PLACEHOLDER;
  }
  const abs = toPaise(Math.abs(value));
  // Sign from the rounded amount, so -0.001 renders "₹0", not "-₹0".
  const sign = value < 0 && abs > 0 ? "-" : "";

  if (mode === "full" || abs < LAKH) {
    const decimals = Number.isInteger(abs) ? 0 : 2;
    return `${sign}₹${grouped(abs, decimals)}`;
  }

  const unit = pickCompactUnit(abs);
  return `${sign}₹${grouped(abs / unit.divisor, unit.decimals)} ${unit.suffix}`;
}
