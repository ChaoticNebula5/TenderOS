// Direct manipulation of the world. On the overview: drag (or swipe sideways on touch)
// to turn the sphere, click a page to fling it away, hold a page to lift it out and
// throw it; it files itself back. In tender analysis: hover and click mandatory pages.
// In submission: grab the top page and throw it, or click to toss it aside; it slides
// back under the stack. Pages being held or thrown fly in #flying, above the content.

import { N, isM } from "../data/tender";
import { bringToFront } from "./actions";
import { NC } from "./arrangements";
import { CH, CW, RM, clamp, hash, lerp } from "./math";
import { W, view } from "./layout";
import { ctx, drag, els, input, ov, rv, scr, stack, type DragKind, type Overlay } from "./store";

/** Release speed above which a held page is thrown rather than set back, px/s. */
const THROW = 380;

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

// pick() filters, defined once so a pointermove allocates nothing
const isMand = (i: number) => isM[i];
const inStack = (i: number) => stack.of[i] >= 0;

/**
 * The front-most visible document under (x, y), or -1. Tests the rotated card rectangle
 * from this frame's screen position; `test` narrows which documents qualify. Pages out
 * of their arrangement (held, thrown, returning) are never picked.
 */
export function pick(x: number, y: number, test?: (i: number) => boolean) {
  let best = -1,
    bz = -Infinity;
  for (let i = 0; i < N; i++) {
    if (scr.o[i] < 0.35 || ov[i] || (test && !test(i))) continue;
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

// ---------------------------------------------------------------------------------
// Overlays: a page out of its arrangement. Each moves between #cards and #flying
// exactly twice: lift() when it is grabbed or flung, land() when it starts back.

function lift(i: number) {
  if (els.flying && els.wrap[i]) els.flying.appendChild(els.wrap[i]);
}
function land(i: number) {
  if (els.cards && els.wrap[i]) els.cards.appendChild(els.wrap[i]);
  els.card[i]?.classList.remove("held");
}

function overlay(i: number, kind: DragKind, s: number, sT: number, o: number): Overlay {
  return {
    mode: "drag", kind, x: scr.x[i], y: scr.y[i], s, sT, r: scr.r[i], o,
    vx: 0, vy: 0, vr: 0, t: 0, w: 1, delay: kind === "pile" ? 1.6 : 0.25, away: false, exitX: 0,
  };
}

/** Lift page i out under the pointer at (x, y): larger, held shadow, following the pointer. */
function grab(i: number, x: number, y: number, kind: DragKind) {
  ov[i] = overlay(i, kind, scr.s[i], Math.min(1, scr.s[i] * 1.3 + 0.05), 1);
  lift(i);
  els.card[i]?.classList.add("held");
  drag.mode = "card";
  drag.id = i;
  drag.gx = x - scr.x[i];
  drag.gy = y - scr.y[i];
}

/** Move page i to the bottom of the submission stack. */
function toBottom(i: number) {
  const k = stack.order.indexOf(i);
  if (k < 0) return;
  stack.order.splice(k, 1);
  stack.order.unshift(i);
  stack.order.forEach((id, j) => (stack.of[id] = j));
}

/** Throw page i (already an overlay in #flying) with velocity (vx, vy). */
function launch(i: number, vx: number, vy: number) {
  const v = ov[i];
  if (!v) return;
  v.mode = "fly";
  v.vx = vx;
  v.vy = vy;
  v.vr = vx * 0.22 + (hash(ctx.T * 9) - 0.5) * 160;
  v.t = 0;
  v.away = v.kind === "pile";
  els.card[i]?.classList.remove("held");
  if (v.kind === "stack") toBottom(i);
}

let throwDir = 1;

/** Velocity for a click or tap: outward from the world centre on the overview, sideways (alternating) on the stack. */
function flickVelocity(i: number, kind: DragKind): [number, number] {
  if (kind === "pile") {
    const dx = scr.x[i] - W.cx,
      dy = scr.y[i] - W.cy,
      d = Math.hypot(dx, dy) || 1,
      sp = 1300 + hash(ctx.T * 7) * 500;
    return [(dx / d) * sp, (dy / d) * sp - 380];
  }
  throwDir = -throwDir;
  return [throwDir * (1500 + hash(ctx.T * 3) * 600), -420 - hash(ctx.T * 5) * 260];
}

/** Fling page i from where it rests. */
function flick(i: number, kind: DragKind) {
  if (i < 0 || ov[i]) return;
  const [vx, vy] = flickVelocity(i, kind);
  ov[i] = overlay(i, kind, scr.s[i], scr.s[i], Math.max(0.6, scr.o[i]));
  lift(i);
  launch(i, vx, vy);
}

/** Send a held page back to its place without throwing it. */
function setBack(i: number) {
  const v = ov[i];
  if (!v || v.mode !== "drag") return;
  v.mode = "return";
  v.w = 1;
  land(i);
}

/**
 * Advance overlay v of page i by dt. (sx, sy, sc, r) is where the page's arrangement puts
 * it this frame, so a returning page can come back to it. Returns true once the page is
 * home and the overlay can be dropped.
 */
export function stepOv(v: Overlay, i: number, dt: number, sx: number, sy: number, sc: number, r: number) {
  if (v.mode === "drag") {
    v.s += (v.sT - v.s) * (1 - Math.exp(-dt * 10));
    v.r += (clamp(v.vx * 0.012, -16, 16) - v.r) * (1 - Math.exp(-dt * 8)); // tilts with its speed
  } else if (v.mode === "fly") {
    v.t += dt;
    v.x += v.vx * dt;
    v.y += v.vy * dt;
    v.vy += 560 * dt; // gravity
    const k = Math.exp(-dt * 0.35); // air drag
    v.vx *= k;
    v.vy *= k;
    v.r += v.vr * dt;
    v.vr *= Math.exp(-dt * 0.4);
    if (v.away) {
      v.s *= Math.exp(-dt * 0.8);
      v.o = Math.min(v.o, clamp(1.7 - v.t, 0, 1));
    }
    const m = CW * v.s;
    if (v.x < -m || v.x > view.vw + m || v.y > view.vh + m || v.y < -2 * m || v.t > 2.8 || v.o <= 0) {
      v.mode = "gone";
      v.t = 0;
      v.exitX = v.x;
    }
  } else if (v.mode === "gone") {
    v.t += dt;
    if (v.t > v.delay) {
      v.mode = "return";
      v.w = 1;
      land(i);
      if (v.kind === "pile") {
        // reappear small and transparent at its slot, and grow back into it
        v.x = sx;
        v.y = sy;
        v.s = sc * 0.15;
        v.o = 0;
        v.r = r + (hash(i) - 0.5) * 60;
      } else {
        // slide back in from the side it left
        const side = v.exitX > W.cx ? 1 : -1;
        v.x = side > 0 ? view.vw + CW * sc : -CW * sc;
        v.y = sy - 30;
        v.s = sc;
        v.o = 1;
        v.r = side * 28;
      }
    }
  } else {
    v.w -= dt / (v.kind === "pile" ? 1.1 : 0.8);
    if (v.w <= 0) return true;
  }
  return false;
}

/** Return every page out of its arrangement to #cards at once. Used when the world stops. */
export function landAll() {
  for (let i = 0; i < N; i++) if (ov[i]) land(i);
}

// ---------------------------------------------------------------------------------
// Pointer handling

function onDown(e: PointerEvent) {
  if (e.button !== 0 || overUI(e.target)) return;
  const x = e.clientX,
    y = e.clientY,
    touch = e.pointerType === "touch";
  if (!inWorld(x, y) || drag.mode) return;
  const ch = Math.round(input.curP),
    now = performance.now();
  if (ch === 1) {
    // tender analysis: a mandatory page spins to the front
    const hit = pick(x, y, isMand);
    if (hit >= 0) {
      e.preventDefault();
      bringToFront(hit);
    }
    return;
  }
  const start = (kind: DragKind, hit: number) => {
    drag.mode = touch ? "tap" : "pending";
    drag.kind = kind;
    drag.hit = hit;
    drag.sx = drag.lx = x;
    drag.sy = drag.ly = y;
    drag.t0 = drag.lt = now;
    drag.vx = drag.vy = 0;
  };
  if (ch === 0) {
    const hit = pick(x, y);
    start("pile", hit);
    if (!touch) {
      e.preventDefault();
      clearTimeout(drag.timer);
      // holding still on a page lifts it out
      if (hit >= 0)
        drag.timer = window.setTimeout(() => {
          if (drag.mode === "pending" && !ov[hit]) grab(hit, drag.lx, drag.ly, "pile");
        }, 220);
    }
  } else if (ch === NC - 1) {
    if (pick(x, y, inStack) < 0) return;
    const top = stack.order[stack.order.length - 1];
    if (ov[top]) return;
    start("stack", top);
    if (!touch) {
      // the top page comes up into the hand at once
      e.preventDefault();
      grab(top, x, y, "stack");
    }
  }
  if (drag.mode && !touch) setBody("grabbing");
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
    // a mouse that is no longer pressed: the release happened somewhere we did not hear it
    if (mouse && e.buttons === 0) return abortDrag();
    const dtm = Math.max(4, now - drag.lt) / 1000;
    drag.vx = lerp(drag.vx, (x - drag.lx) / dtm, 0.4);
    drag.vy = lerp(drag.vy, (y - drag.ly) / dtm, 0.4);
    if (drag.mode === "pending" && Math.hypot(x - drag.sx, y - drag.sy) > 6) {
      clearTimeout(drag.timer);
      drag.mode = "rotate";
    }
    if (drag.mode === "rotate") {
      const dx = x - drag.lx,
        dy = y - drag.ly;
      ctx.dyaw += dx * 0.0055;
      ctx.dpitch = clamp(ctx.dpitch + dy * 0.004, -1, 1);
      rv.y = lerp(rv.y, (dx * 0.0055) / dtm, 0.4);
      rv.p = lerp(rv.p, (dy * 0.004) / dtm, 0.4);
    } else if (drag.mode === "card") {
      const v = ov[drag.id];
      if (v) {
        v.x = x - drag.gx;
        v.y = y - drag.gy;
        v.vx = drag.vx;
      }
    } else if (drag.mode === "tap") {
      const ax = Math.abs(x - drag.sx),
        ay = Math.abs(y - drag.sy);
      if (drag.kind === "pile" && ax > 10 && ax > ay * 1.4) drag.mode = "rotate"; // a sideways swipe turns the sphere
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
    } else if (ch === NC - 1 && pick(x, y, inStack) >= 0) c = "grab";
  }
  setBody(c);
}

function onUp(e: PointerEvent) {
  if (!drag.mode) return;
  clearTimeout(drag.timer);
  const now = performance.now();
  const quick = now - drag.t0 < 280 && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 8;
  const still = now - drag.lt > 90; // held still before letting go: no throw
  if (drag.mode === "rotate") {
    if (still) {
      rv.y = 0;
      rv.p = 0;
    }
  } else if (drag.mode === "pending" || drag.mode === "tap") {
    if (quick) flick(drag.hit, drag.kind);
  } else if (drag.mode === "card") {
    const i = drag.id,
      v = ov[i];
    if (v) {
      const sp = still ? 0 : Math.hypot(drag.vx, drag.vy);
      if (quick && drag.kind === "stack") {
        // a click on the stack tosses the top page aside, straight from the hand
        const [vx, vy] = flickVelocity(i, "stack");
        launch(i, vx, vy);
      } else if (sp > THROW) launch(i, drag.vx, drag.vy);
      else setBack(i);
    }
  }
  drag.mode = null;
  setBody("");
}

/**
 * End the gesture without acting on it: no fling, no throw; a held page goes back to its
 * place. For pointercancel (the browser took the touch for scrolling), losing focus, the
 * tab hiding, and a mouse move that shows the button is no longer down.
 */
function abortDrag() {
  if (!drag.mode) return;
  clearTimeout(drag.timer);
  if (drag.mode === "card") setBack(drag.id);
  else if (drag.mode === "rotate") {
    rv.y = 0;
    rv.p = 0;
  }
  drag.mode = null;
  setBody("");
}

/** Attach every pointer and focus handler; all are removed when `signal` aborts. */
export function attachPointer(signal: AbortSignal) {
  addEventListener("pointerdown", onDown, { signal });
  addEventListener("pointermove", onMove, { signal, passive: true });
  addEventListener("pointerup", onUp, { signal });
  addEventListener("pointercancel", abortDrag, { signal });
  addEventListener("blur", abortDrag, { signal });
  document.addEventListener(
    "visibilitychange",
    () => {
      if (document.hidden) abortDrag();
    },
    { signal },
  );
  signal.addEventListener(
    "abort",
    () => {
      clearTimeout(drag.timer);
      drag.mode = null;
      setBody("");
    },
    { once: true },
  );
}
