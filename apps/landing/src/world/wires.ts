// SVG wires between documents. Defined here as data; Wires.tsx renders the paths
// once; placeWires() writes their geometry each frame, only when it changes.

import { REQ, type Status } from "../data/tender";
import { CH, CW } from "./math";
import { scr } from "./store";

export type Pt = { x: number; y: number };

/** Write a path's d attribute only when it changed. Returns whether it did. */
export function setD(el: SVGPathElement, d: string, cache: { d: string }) {
  if (cache.d === d) return false;
  el.setAttribute("d", d);
  cache.d = d;
  return true;
}

/** Write an element's opacity only when its two-decimal string changed. */
export function setO(el: SVGElement, o: number, cache: { o: string }) {
  const s = o.toFixed(2);
  if (cache.o === s) return;
  el.style.opacity = s;
  cache.o = s;
}

/** Cubic between two points, leaving and arriving vertically or horizontally. */
export const curve = (a: Pt, b: Pt, vert: boolean) =>
  vert
    ? `M${a.x} ${a.y}C${a.x} ${(a.y + b.y) / 2} ${b.x} ${(a.y + b.y) / 2} ${b.x} ${b.y}`
    : `M${a.x} ${a.y}C${(a.x + b.x) / 2} ${a.y} ${(a.x + b.x) / 2} ${b.y} ${b.x} ${b.y}`;

/** Point at parameter t on the cubic a, c1, c2, b. */
export function cubicAt(a: Pt, c1: Pt, c2: Pt, b: Pt, t: number): Pt {
  const u = 1 - t,
    k0 = u * u * u,
    k1 = 3 * u * u * t,
    k2 = 3 * u * t * t,
    k3 = t * t * t;
  return { x: k0 * a.x + k1 * c1.x + k2 * c2.x + k3 * b.x, y: k0 * a.y + k1 * c1.y + k2 * c2.y + k3 * b.y };
}

/**
 * Length of the cubic a, c1, c2, b, computed instead of read from the DOM
 * (getTotalLength), so drawing a wire never asks the browser for geometry mid-frame.
 * A 32-segment polyline underestimates by well under 0.1% on these gentle curves;
 * the caller pads the dash to cover it.
 */
export function cubicLen(a: Pt, c1: Pt, c2: Pt, b: Pt): number {
  let len = 0,
    px = a.x,
    py = a.y;
  for (let k = 1; k <= 32; k++) {
    const p = cubicAt(a, c1, c2, b, k / 32);
    len += Math.hypot(p.x - px, p.y - py);
    px = p.x;
    py = p.y;
  }
  return len;
}

/** Anchor points just outside a document's edges, from this frame's screen position. */
export const bot = (i: number): Pt => ({ x: scr.x[i], y: scr.y[i] + (CH * scr.s[i]) / 2 + 3 });
export const topP = (i: number): Pt => ({ x: scr.x[i], y: scr.y[i] - (CH * scr.s[i]) / 2 - 3 });
export const rgt = (i: number): Pt => ({ x: scr.x[i] + (CW * scr.s[i]) / 2 + 3, y: scr.y[i] });
export const lft = (i: number): Pt => ({ x: scr.x[i] - (CW * scr.s[i]) / 2 - 3, y: scr.y[i] });

/** Snap to half pixels, so sub-pixel drift does not rewrite a path every frame. */
const P2 = (p: Pt): Pt => ({ x: Math.round(p.x * 2) / 2, y: Math.round(p.y * 2) / 2 });

/** Eligibility wires: requirement page to each piece of evidence that settled it. */
export const MATCH_WIRES: readonly { a: number; b: number; st: Status }[] = REQ.flatMap((r) =>
  r.ev.map((b) => ({ a: r.pg, b, st: r.st })),
);
/** The requirement with no evidence gets a short dead-end stub. */
export const GAP_PAGE = REQ.find((r) => r.st === "gap")!.pg;

type Wire = { el: SVGPathElement | null; d: string; o: string };
const wire = (): Wire => ({ el: null, d: "", o: "" });

export const matchWire: Wire[] = MATCH_WIRES.map(wire);
export const gapWire: Wire = wire();

export function resetWires() {
  for (const w of [...matchWire, gapWire]) {
    w.d = "";
    w.o = "";
    w.el?.removeAttribute("d");
  }
}

/** Eligibility wires, faded by the eligibility chapter's weight `am`. */
export function placeWires(am: number) {
  for (let k = 0; k < MATCH_WIRES.length; k++) {
    const w = matchWire[k];
    if (!w.el) continue;
    if (am < 0.01) {
      setD(w.el, "", w);
      continue;
    }
    const m = MATCH_WIRES[k];
    setD(w.el, curve(P2(bot(m.a)), P2(topP(m.b)), true), w);
    setO(w.el, am, w);
  }
  if (!gapWire.el) return;
  if (am < 0.01) {
    setD(gapWire.el, "", gapWire);
    return;
  }
  const g = P2(bot(GAP_PAGE));
  setD(gapWire.el, `M${g.x} ${g.y}L${g.x} ${g.y + 46}`, gapWire);
  setO(gapWire.el, am, gapWire);
}
