// Shared constants and pure helpers. Nothing here touches the DOM.

const reducedQuery =
  typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;

/** Reduced motion, read live: while it is on, the page snaps instead of animating. */
export const motion = { reduced: reducedQuery?.matches ?? false };

/** Follow the reduced-motion preference as it changes, until `signal` aborts. */
export function watchMotion(signal: AbortSignal, onChange: (reduced: boolean) => void) {
  motion.reduced = reducedQuery?.matches ?? false;
  reducedQuery?.addEventListener(
    "change",
    (e) => {
      motion.reduced = e.matches;
      onChange(e.matches);
    },
    { signal },
  );
}

/** Natural card size: the size a document is read at. On-screen scale never exceeds 1. */
export const CW = 320;
export const CH = 436;
/** Focal length in world px. */
export const F = 1100;
export const TAU = Math.PI * 2;

export const clamp = (v: number, a: number, b: number) => (v < a ? a : v > b ? b : v);
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
/** Deterministic 0..1 noise, so every document's jitter is the same on every load. */
export const hash = (n: number) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};
