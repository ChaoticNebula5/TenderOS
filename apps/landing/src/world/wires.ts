// SVG wires between documents. Defined here as data; Wires.tsx renders the paths
// once; placeWires() writes their geometry each frame, only when it changes.

import { EDGES, NODES, UNREACHED } from "../data/impact";
import { REQ, type Status } from "../data/tender";
import { CH, CW, clamp, ease } from "./math";
import { ctx, input, scr } from "./store";

export type Pt = { x: number; y: number };

/** Write a path's d attribute only when it changed. Returns whether it did. */
function setD(el: SVGPathElement, d: string, cache: { d: string }) {
  if (cache.d === d) return false;
  el.setAttribute("d", d);
  cache.d = d;
  return true;
}

/** Write an element's opacity only when its two-decimal string changed. */
function setO(el: SVGElement, o: number, cache: { o: string }) {
  const s = o.toFixed(2);
  if (cache.o === s) return;
  el.style.opacity = s;
  cache.o = s;
}

/** Control points of a cubic between two points, leaving and arriving vertically or horizontally. */
const curveCtl = (a: Pt, b: Pt, vert: boolean): [Pt, Pt, Pt, Pt] =>
  vert
    ? [a, { x: a.x, y: (a.y + b.y) / 2 }, { x: b.x, y: (a.y + b.y) / 2 }, b]
    : [a, { x: (a.x + b.x) / 2, y: a.y }, { x: (a.x + b.x) / 2, y: b.y }, b];

/** Path string for a cubic's control points. */
const cubicD = ([a, c1, c2, b]: readonly Pt[]) => `M${a.x} ${a.y}C${c1.x} ${c1.y} ${c2.x} ${c2.y} ${b.x} ${b.y}`;

/** Cubic between two points as a path string. */
const curve = (a: Pt, b: Pt, vert: boolean) => cubicD(curveCtl(a, b, vert));

/** Point at parameter t on the cubic a, c1, c2, b. */
function cubicAt(a: Pt, c1: Pt, c2: Pt, b: Pt, t: number): Pt {
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

/**
 * The point a fraction `frac` of the way along the cubic by arc length, which is where a
 * dash drawn to that fraction ends. (cubicAt's t is the curve parameter, which runs
 * faster on the straighter parts, so it would put the pulse ahead of or behind the head.)
 * Walks the same 32-segment polyline as cubicLen().
 */
function cubicAtLen(a: Pt, c1: Pt, c2: Pt, b: Pt, frac: number, len: number): Pt {
  const target = clamp(frac, 0, 1) * len;
  let acc = 0,
    prev = a;
  for (let k = 1; k <= 32; k++) {
    const p = cubicAt(a, c1, c2, b, k / 32),
      seg = Math.hypot(p.x - prev.x, p.y - prev.y);
    if (acc + seg >= target) {
      const u = seg > 0 ? (target - acc) / seg : 0;
      return { x: prev.x + (p.x - prev.x) * u, y: prev.y + (p.y - prev.y) * u };
    }
    acc += seg;
    prev = p;
  }
  return b;
}

/** Anchor points just outside a document's edges, from this frame's screen position. */
const bot = (i: number): Pt => ({ x: scr.x[i], y: scr.y[i] + (CH * scr.s[i]) / 2 + 3 });
const topP = (i: number): Pt => ({ x: scr.x[i], y: scr.y[i] - (CH * scr.s[i]) / 2 - 3 });
const rgt = (i: number): Pt => ({ x: scr.x[i] + (CW * scr.s[i]) / 2 + 3, y: scr.y[i] });
const lft = (i: number): Pt => ({ x: scr.x[i] - (CW * scr.s[i]) / 2 - 3, y: scr.y[i] });

/** Snap to half pixels, so sub-pixel drift does not rewrite a path every frame. */
const P2 = (p: Pt): Pt => ({ x: Math.round(p.x * 2) / 2, y: Math.round(p.y * 2) / 2 });

/** Eligibility wires: requirement page to each piece of evidence that settled it. */
export const MATCH_WIRES: readonly { a: number; b: number; st: Status }[] = REQ.flatMap((r) =>
  r.ev.map((b) => ({ a: r.pg, b, st: r.st })),
);
/** The requirement with no evidence gets a short dead-end stub. */
const GAP_PAGE = REQ.find((r) => r.st === "gap")!.pg;

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

/**
 * Change impact edges. Each has a base wire, a hot wire that draws along it as the wave
 * travels, and a pulse dot at the hot wire's head. `p` is how much of the hot wire is drawn.
 */
type ImpactEdge = {
  el: SVGPathElement | null;
  hot: SVGPathElement | null;
  dot: SVGCircleElement | null;
  p: number; // how much of the hot wire is drawn, 0..1
  base: { d: string; o: string }; // base wire caches
  hotC: { d: string; o: string; dash: string; off: string; len: number }; // hot wire caches
  dotC: { o: string; xy: string }; // pulse caches
};
const freshEdge = (): Omit<ImpactEdge, "el" | "hot" | "dot"> => ({
  p: 0,
  base: { d: "", o: "" },
  hotC: { d: "", o: "", dash: "", off: "", len: 0 },
  dotC: { o: "", xy: "" },
});
export const impactEdge: ImpactEdge[] = EDGES.map(() => ({ el: null, hot: null, dot: null, ...freshEdge() }));

function hotOff(e: ImpactEdge) {
  if (e.hot) setO(e.hot, 0, e.hotC);
  if (e.dot) setO(e.dot, 0, e.dotC);
}

/**
 * Change impact edges, faded by the chapter's weight `ai`. An edge is on the wave when
 * its upstream node is reached and its downstream node is exactly one hop further; its
 * hot wire then draws as the wave crosses that hop, and eases back (exp(-6 dt)) when the
 * cause changes. Lengths and pulse positions are computed from the control points.
 */
export function placeImpactEdges(ai: number, dt: number) {
  const { cause, hop, wave } = input;
  for (let k = 0; k < EDGES.length; k++) {
    const e = impactEdge[k];
    if (!e.el || !e.hot || !e.dot) continue;
    if (ai < 0.01) {
      setD(e.el, "", e.base);
      hotOff(e);
      continue;
    }
    const [na, nb] = EDGES[k];
    const a = NODES[na].id,
      b = NODES[nb].id;
    const ctl = ctx.portrait ? curveCtl(P2(bot(a)), P2(topP(b)), true) : curveCtl(P2(rgt(a)), P2(lft(b)), false);
    const d = cubicD(ctl);
    setD(e.el, d, e.base);
    setO(e.el, ai, e.base);

    const on = cause >= 0 && hop[na] < UNREACHED && hop[nb] === hop[na] + 1;
    const target = on ? clamp(wave - hop[na], 0, 1) : 0;
    e.p = target >= e.p ? target : e.p + (target - e.p) * (1 - Math.exp(-dt * 6));
    if (e.p < 0.003) {
      hotOff(e);
      continue;
    }
    const h = e.hotC;
    // the hot wire follows the base wire; its dash is one (computed) length long
    if (setD(e.hot, d, h)) {
      h.len = cubicLen(ctl[0], ctl[1], ctl[2], ctl[3]) * 1.002 + 1;
      const dash = h.len.toFixed(1);
      if (dash !== h.dash) {
        e.hot.style.strokeDasharray = `${dash} ${dash}`;
        h.dash = dash;
      }
    }
    const pe = ease(e.p),
      off = (h.len * (1 - pe)).toFixed(1);
    if (off !== h.off) {
      e.hot.style.strokeDashoffset = off;
      h.off = off;
    }
    setO(e.hot, ai, h);
    // the pulse rides the head of the hot wire while it is still travelling
    if (on && pe < 0.995) {
      const pt = cubicAtLen(ctl[0], ctl[1], ctl[2], ctl[3], pe, h.len),
        x = pt.x.toFixed(1),
        y = pt.y.toFixed(1);
      if (`${x} ${y}` !== e.dotC.xy) {
        e.dot.setAttribute("cx", x);
        e.dot.setAttribute("cy", y);
        e.dotC.xy = `${x} ${y}`;
      }
      setO(e.dot, ai, e.dotC);
    } else setO(e.dot, 0, e.dotC);
  }
}

export function resetImpactEdges() {
  for (const e of impactEdge) {
    Object.assign(e, freshEdge());
    e.el?.removeAttribute("d");
    e.hot?.removeAttribute("d");
    if (e.hot) e.hot.style.opacity = "0";
    if (e.dot) e.dot.style.opacity = "0";
    e.hotC.o = "0.00";
    e.dotC.o = "0.00";
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
