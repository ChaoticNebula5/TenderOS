// Direct manipulation of the world: drag (or swipe sideways on touch) to turn the
// overview sphere, with inertia after release; hover and click mandatory pages in the
// tender analysis helix; and the matching cursors.

import { N, isM } from "../data/tender";
import { bringToFront } from "./actions";
import { CH, CW, RM, clamp, lerp } from "./math";
import { W, view } from "./layout";
import { ctx, drag, input, rv, scr } from "./store";

/** Pointer targets that belong to the page, not the world: they never start a drag. */
const overUI = (t: EventTarget | null) =>
  t instanceof Element && t.closest(".col, header, footer, a, button") !== null;

const inWorld = (x: number, y: number) =>
  ctx.portrait ? y > W.y && y < W.y + W.h : x > W.x && x < W.x + W.w && y > W.y;

let bodyCls = "";
function setBody(c: string) {
  if (c === bodyCls) return;
  if (bodyCls) document.body.classList.remove(bodyCls);
  if (c) document.body.classList.add(c);
  bodyCls = c;
}

/**
 * The front-most visible document under (x, y), or -1. Tests the rotated card rectangle
 * from this frame's screen position; `test` narrows which documents qualify.
 */
export function pick(x: number, y: number, test?: (i: number) => boolean) {
  let best = -1,
    bz = -Infinity;
  for (let i = 0; i < N; i++) {
    if (scr.o[i] < 0.35 || (test && !test(i))) continue;
    const a = (-scr.r[i] * Math.PI) / 180,
      c = Math.cos(a),
      s = Math.sin(a),
      dx = x - scr.x[i],
      dy = y - scr.y[i];
    if (
      Math.abs(dx * c - dy * s) <= (CW * scr.s[i]) / 2 &&
      Math.abs(dx * s + dy * c) <= (CH * scr.s[i]) / 2 &&
      scr.z[i] > bz
    ) {
      bz = scr.z[i];
      best = i;
    }
  }
  return best;
}

const isMand = (i: number) => isM[i];

function onDown(e: PointerEvent) {
  if (e.button !== 0 || overUI(e.target)) return;
  const x = e.clientX,
    y = e.clientY,
    touch = e.pointerType === "touch";
  if (!inWorld(x, y)) return;
  const ch = Math.round(input.curP);
  if (ch === 1) {
    // tender analysis: a mandatory page spins to the front
    const hit = pick(x, y, isMand);
    if (hit >= 0) {
      e.preventDefault();
      bringToFront(hit);
    }
    return;
  }
  if (ch !== 0) return;
  // touch starts as a tap, because a vertical move must still scroll the page
  Object.assign(drag, { mode: touch ? "tap" : "pending", sx: x, sy: y, lx: x, ly: y, lt: performance.now() });
  if (!touch) {
    e.preventDefault(); // no text selection while dragging
    setBody("grabbing");
  }
}

function onMove(e: PointerEvent) {
  const x = e.clientX,
    y = e.clientY,
    now = performance.now(),
    mouse = e.pointerType === "mouse";
  if (mouse && !RM) {
    input.mxT = (x / view.vw) * 2 - 1;
    input.myT = (y / view.vh) * 2 - 1;
  }
  if (drag.mode) {
    const dtm = Math.max(4, now - drag.lt) / 1000;
    if (drag.mode === "pending" && Math.hypot(x - drag.sx, y - drag.sy) > 6) drag.mode = "rotate";
    if (drag.mode === "rotate") {
      const dx = x - drag.lx,
        dy = y - drag.ly;
      ctx.dyaw += dx * 0.0055;
      ctx.dpitch = clamp(ctx.dpitch + dy * 0.004, -1, 1);
      rv.y = lerp(rv.y, (dx * 0.0055) / dtm, 0.4);
      rv.p = lerp(rv.p, (dy * 0.004) / dtm, 0.4);
    } else if (drag.mode === "tap") {
      const ax = Math.abs(x - drag.sx),
        ay = Math.abs(y - drag.sy);
      if (ax > 10 && ax > ay * 1.4) drag.mode = "rotate"; // a sideways swipe turns the sphere
      else if (ax + ay > 10) drag.mode = null; // anything else is a scroll
    }
    drag.lx = x;
    drag.ly = y;
    drag.lt = now;
    return;
  }
  if (!mouse) return;
  const ch = Math.round(input.curP);
  let c = "";
  input.hoverCard = -1;
  if (!overUI(e.target) && inWorld(x, y)) {
    if (ch === 0) c = "grab";
    else if (ch === 1) {
      input.hoverCard = pick(x, y, isMand);
      if (input.hoverCard >= 0) c = "point";
    }
  }
  setBody(c);
}

function onUp() {
  if (!drag.mode) return;
  // held still before letting go: no coasting
  if (drag.mode === "rotate" && performance.now() - drag.lt > 90) {
    rv.y = 0;
    rv.p = 0;
  }
  drag.mode = null;
  setBody("");
}

/** Attach the pointer handlers; all are removed when `signal` aborts. */
export function attachPointer(signal: AbortSignal) {
  addEventListener("pointerdown", onDown, { signal });
  addEventListener("pointermove", onMove, { signal, passive: true });
  addEventListener("pointerup", onUp, { signal });
  addEventListener("pointercancel", onUp, { signal });
  signal.addEventListener("abort", () => setBody(""), { once: true });
}
