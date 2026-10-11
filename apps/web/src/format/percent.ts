// A 0-1 fraction as a whole percent: 0.8999999761581421 -> "90%".
//
// chosen, not in DESIGN: DESIGN.md (Tables) names the raw-float problem
// (compliance confidence rendered as 0.8999999761581421) but no format.
// Used for compliance confidence and role-match score, both 0-1 by schema
// (agents/schemas/compliance.py:34, agents/schemas/talent.py:56).
//
// Rounds to the nearest integer with two guards, so the ends of the scale
// only mean what they say:
// - "100%" only for exactly 1 (0.996 is "99%", not a certainty it isn't);
// - "0%" only for exactly 0 (0.004 is "1%", not "no confidence at all").
//
// null (no score yet) and non-finite input return null; the caller picks
// the words for "no value", since those depend on the column.

export function formatPercent(value: number | null): string | null {
  if (value === null || !Number.isFinite(value)) return null;
  let whole = Math.round(value * 100);
  if (whole === 100 && value !== 1) whole = 99;
  if (whole === 0 && value !== 0) whole = value > 0 ? 1 : -1;
  return `${whole}%`;
}
