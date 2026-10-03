// Direct manipulation of the world. V1: drag (or swipe sideways on touch) to turn
// the overview sphere, with inertia after release, and the matching cursors.

import { RM, clamp, lerp } from "./math";
import { W, view } from "./layout";
import { ctx, drag, input, rv } from "./store";

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

function onDown(e: PointerEvent) {
  if (e.button !== 0 || overUI(e.target)) return;
  const x = e.clientX,
    y = e.clientY,
    touch = e.pointerType === "touch";
  if (!inWorld(x, y)) return;
  if (Math.round(input.curP) !== 0) return;
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
  setBody(!overUI(e.target) && inWorld(x, y) && Math.round(input.curP) === 0 ? "grab" : "");
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
