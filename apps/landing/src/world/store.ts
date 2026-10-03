// Mutable world state shared by the frame loop, layout and input handlers.
// None of this is React state: the loop reads and writes it every frame, and
// React never re-renders because of it (architecture rule 3).

/** DOM nodes the loop writes to. Filled by ref callbacks during React's commit. */
export const els = {
  wrap: [] as HTMLDivElement[], // .cw: translate, rotate, fine scale
  sc: [] as HTMLDivElement[], // .sc: quantized coarse scale
  card: [] as HTMLDivElement[], // .card: never scaled directly
  atmo: null as HTMLDivElement | null,
  whereN: null as HTMLElement | null,
  whereT: null as HTMLElement | null,
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
};

/** Pointer input that the loop eases toward. */
export const input = {
  curP: 0, // chapter progress from the last frame, used to route pointer events
  mxT: 0, // mouse parallax target
  myT: 0,
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
  Object.assign(ctx, { T: 0, yv: 0, mx: 0, my: 0, portrait: false, dyaw: 0, dpitch: 0 });
  Object.assign(input, { curP: 0, mxT: 0, myT: 0 });
  Object.assign(drag, { mode: null, sx: 0, sy: 0, lx: 0, ly: 0, lt: 0 });
  Object.assign(rv, { y: 0, p: 0 });
}
