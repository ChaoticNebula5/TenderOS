// Mutable world state shared by the frame loop, layout and input handlers.
// None of this is React state: the loop reads and writes it every frame, and
// React never re-renders because of it (architecture rule 3).

import { N, NP } from "../data/tender";

/** DOM nodes the loop writes to. Filled by ref callbacks during React's commit. */
export const els = {
  wrap: [] as HTMLDivElement[], // .cw: translate, rotate, fine scale
  sc: [] as HTMLDivElement[], // .sc: quantized coarse scale
  card: [] as HTMLDivElement[], // .card: never scaled directly
  atmo: null as HTMLDivElement | null,
  scrimL: null as HTMLDivElement | null,
  scrimR: null as HTMLDivElement | null,
  wires: null as SVGSVGElement | null,
  whereN: null as HTMLElement | null,
  whereT: null as HTMLElement | null,
  idxBtn: [] as HTMLButtonElement[], // tender analysis index rows, by MAND position
};

/** Inputs to the arrangement functions. */
export const ctx = {
  T: 0, // seconds since start
  yv: 0, // smoothed scroll in viewport heights
  mx: 0, // eased mouse parallax, -1..1
  my: 0,
  portrait: false,
  dyaw: 0, // accumulated drag rotation of the overview sphere
  dpitch: 0,
  y1: 1, // start of the tender analysis hold, in viewport heights: the helix's rest position
  hoff: 0, // eased helix offset that brings a chosen page to the front
};

/** Where each document landed on screen this frame: read by labels, wires and picking. */
export const scr = {
  x: new Float32Array(N), // centre
  y: new Float32Array(N),
  s: new Float32Array(N), // final on-screen scale
  o: new Float32Array(N), // opacity
  r: new Float32Array(N), // rotation, degrees
  z: new Float32Array(N), // stacking order
};

/** How squarely each tender page faces the reader in the helix, 0..1. */
export const HF = new Float32Array(NP);

/** Pointer input that the loop eases toward, and hover/selection state from the page. */
export const input = {
  curP: 0, // chapter progress from the last frame, used to route pointer events
  mxT: 0, // mouse parallax target
  myT: 0,
  idxHover: -1, // index row under the pointer or keyboard focus, by MAND position
  hoverCard: -1, // mandatory page under the pointer in the helix, by card id
  hoffT: 0, // helix offset target set by bringToFront()
};

export type DragMode = null | "pending" | "rotate" | "tap";

/** The active pointer gesture on the world. */
export const drag = {
  mode: null as DragMode,
  sx: 0, // start position
  sy: 0,
  lx: 0, // last position and time
  ly: 0,
  lt: 0,
};

/** Rotation velocity of the overview sphere, for inertia after a drag. */
export const rv = { y: 0, p: 0 };

/** Back to the state of a fresh page load. Called when the world starts. */
export function resetStore() {
  Object.assign(ctx, { T: 0, yv: 0, mx: 0, my: 0, portrait: false, dyaw: 0, dpitch: 0, y1: 1, hoff: 0 });
  Object.assign(input, { curP: 0, mxT: 0, myT: 0, idxHover: -1, hoverCard: -1, hoffT: 0 });
  Object.assign(drag, { mode: null, sx: 0, sy: 0, lx: 0, ly: 0, lt: 0 });
  Object.assign(rv, { y: 0, p: 0 });
  for (const a of Object.values(scr)) a.fill(0);
  HF.fill(0);
}
