// Mutable world state shared by the frame loop, layout and input handlers.
// None of this is React state: the loop reads and writes it every frame, and
// React never re-renders because of it (architecture rule 3).

import { N, NP, STACK } from "../data/tender";

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
  cards: null as HTMLDivElement | null, // #cards: where documents rest
  flying: null as HTMLDivElement | null, // #flying: above all content, for held and thrown pages
  idxBtn: [] as HTMLButtonElement[], // tender analysis index rows, by MAND position
  claimBtn: [] as HTMLButtonElement[], // evidence tracing statements, by CLAIMS index
};

/** The colour theme; the depth fade depends on it. Set by toggleTheme(). */
export const theme = { light: false };

/** Switch between the dark (default) and light themes: one attribute on the root, one label. */
export function toggleTheme(button: HTMLElement) {
  theme.light = !theme.light;
  document.documentElement.dataset.theme = theme.light ? "light" : "dark";
  button.textContent = theme.light ? "Dark" : "Light";
}

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

/** Vertical offset of each document's cited line (`.ct`) from its centre, at scale 1. Set by measure(). */
export const CT = new Float32Array(N);

/** Pointer input that the loop eases toward, and hover/selection state from the page. */
export const input = {
  curP: 0, // chapter progress from the last frame, used to route pointer events
  mxT: 0, // mouse parallax target
  myT: 0,
  idxHover: -1, // index row under the pointer or keyboard focus, by MAND position
  hoverCard: -1, // mandatory page under the pointer in the helix, by card id
  hoffT: 0, // helix offset target set by bringToFront()
  sel: -1, // selected statement in evidence tracing, by CLAIMS index; mirrors the chapter's React state
  threadP: 0, // how much of the evidence thread is drawn, 0..1; restarts on every selection
  cause: -1, // selected change in change impact, by CAUSES index; mirrors the chapter's React state
  causeNode: -1, // NODES index of that cause's source document
  hop: [] as number[], // hops from the cause to each NODES entry, from seedImpact(); UNREACHED if never
  wave: 0, // how far the impact wave has travelled, in hops
};

/**
 * pending: mouse down on the overview, not yet a rotate or a hold. rotate: turning the
 * sphere. tap: touch down, not yet a swipe or a scroll. card: holding a page.
 */
type DragMode = null | "pending" | "rotate" | "tap" | "card";
export type DragKind = "pile" | "stack";

/** The active pointer gesture on the world. */
export const drag = {
  mode: null as DragMode,
  kind: "pile" as DragKind,
  id: -1, // page being held (mode card)
  hit: -1, // page under the pointer at pointer down, for a click or tap
  sx: 0, // start position and time
  sy: 0,
  t0: 0,
  lx: 0, // last position and time
  ly: 0,
  lt: 0,
  vx: 0, // smoothed pointer velocity, px/s
  vy: 0,
  gx: 0, // grab offset from the page centre
  gy: 0,
  timer: 0, // hold timer on the overview
};

/**
 * A page taken out of its arrangement: held (drag), thrown (fly), off screen waiting
 * to come back (gone), or easing back into its place (return).
 */
export type Overlay = {
  mode: "drag" | "fly" | "gone" | "return";
  kind: DragKind;
  x: number; // screen position, scale, rotation, opacity of the overlay
  y: number;
  s: number;
  sT: number; // target scale while held
  r: number;
  o: number;
  vx: number; // velocity, px/s and deg/s
  vy: number;
  vr: number;
  t: number; // seconds in the current mode
  w: number; // return weight, 1 to 0
  delay: number; // seconds off screen before returning
  away: boolean; // thrown away from the overview: shrinks and fades
  exitX: number; // where it left the screen
};
/** Overlays by card id; null while a page rests in its arrangement. */
export const ov: (Overlay | null)[] = new Array(N).fill(null);

/** Submission stack, bottom to top, and each card's place in it (-1 if not in the stack). */
export const stack = { order: [] as number[], of: new Int16Array(N).fill(-1) };

/** Rotation velocity of the overview sphere, for inertia after a drag. */
export const rv = { y: 0, p: 0 };

/** Back to the state of a fresh page load. Called when the world starts. */
export function resetStore() {
  Object.assign(ctx, { T: 0, yv: 0, mx: 0, my: 0, portrait: false, dyaw: 0, dpitch: 0, y1: 1, hoff: 0 });
  Object.assign(input, {
    curP: 0, mxT: 0, myT: 0, idxHover: -1, hoverCard: -1, hoffT: 0,
    sel: -1, threadP: 0, cause: -1, causeNode: -1, hop: [], wave: 0,
  });
  clearTimeout(drag.timer);
  Object.assign(drag, {
    mode: null, kind: "pile", id: -1, hit: -1, sx: 0, sy: 0, t0: 0, lx: 0, ly: 0, lt: 0, vx: 0, vy: 0, gx: 0, gy: 0, timer: 0,
  });
  ov.fill(null);
  stack.order = [...STACK];
  stack.of.fill(-1);
  stack.order.forEach((id, k) => (stack.of[id] = k));
  Object.assign(rv, { y: 0, p: 0 });
  for (const a of Object.values(scr)) a.fill(0);
  HF.fill(0);
}
