/** Shared per-frame math. Ported from reference-landing.html:554-556. */

export function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** Cubic ease-in-out, used to soften a linear blend amount before it drives
 *  position/opacity lerps. */
export function ease(e: number): number {
  return e < 0.5 ? 4 * e * e * e : 1 - Math.pow(-2 * e + 2, 3) / 2;
}

/**
 * Fraction to move a value toward its target this frame for a frame-rate-
 * independent exponential approach: `value += (target - value) * approach(rate, dt)`.
 * `rate` is "how many multiples of itself per second" - not a duration.
 */
export function approach(rate: number, dt: number): number {
  return 1 - Math.exp(-rate * dt);
}

/** Rule 2: every animation advances by elapsed seconds, clamped at 0.05 -
 *  applied once, here, to the raw delta every per-frame caller receives from
 *  `useFrame`, so nothing downstream has to remember to clamp it itself. */
export function clampedDelta(rawDeltaSeconds: number): number {
  return Math.min(rawDeltaSeconds, 0.05);
}
