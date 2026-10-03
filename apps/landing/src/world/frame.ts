// The one frame loop (architecture rule 2) and the world's lifecycle.

import { N } from "../data/tender";
import { NAMES } from "../data/chapters";
import { DL, L, NC, ZMAX, type Pose } from "./arrangements";
import { W, measure, pOf, view } from "./layout";
import { attachPointer } from "./manipulation";
import { F, RM, clamp, ease, lerp } from "./math";
import { ctx, drag, els, input, resetStore, rv } from "./store";

/** Coarse scale steps per e-fold: about 4%, so the GPU never stretches a page more than 2%. */
const QSTEP = 24;

const A: Pose = { x: 0, y: 0, z: 0, r: 0, o: 0 };
const B: Pose = { x: 0, y: 0, z: 0, r: 0, o: 0 };

// Write caches: a style is written only when its string changes (rule 8).
const cTf: string[] = new Array(N).fill("");
const cOp: string[] = new Array(N).fill("");
const cZ = new Int32Array(N);
const cQ = new Float32Array(N);
const vis = new Uint8Array(N);
let atmoTf = "";
let lastNear = -1;

let last = 0;
let ys = 0;

function resetCaches() {
  cTf.fill("");
  cOp.fill("");
  cZ.fill(0);
  cQ.fill(0);
  vis.fill(0);
  atmoTf = "";
  lastNear = -1;
}

function frame(now: number) {
  const dt = Math.min((now - last) / 1000, 0.05);
  last = now;
  if (document.hidden) return;
  ctx.T += dt;

  // scroll is smoothed, so a wheel notch glides instead of jumping
  ys = RM ? scrollY : ys + (scrollY - ys) * (1 - Math.exp(-dt * 7));
  if (!drag.mode) {
    const k3 = 1 - Math.exp(-dt * 3);
    ctx.mx += (input.mxT - ctx.mx) * k3;
    ctx.my += (input.myT - ctx.my) * k3;
  }
  if (drag.mode !== "rotate") {
    // the sphere coasts after a drag, and its tilt settles back
    ctx.dyaw += rv.y * dt;
    ctx.dpitch += rv.p * dt;
    const k = Math.exp(-dt * 2.4);
    rv.y *= k;
    rv.p *= k;
    ctx.dpitch *= Math.exp(-dt * 0.5);
  }
  ctx.dpitch = clamp(ctx.dpitch, -1, 1);
  ctx.yv = ys / view.vh;
  const FADE = document.documentElement.dataset.theme === "light" ? 0.4 : 0.72;

  const P = pOf(ys);
  input.curP = P;
  const c = Math.min(Math.floor(P), NC - 2),
    f = P - c;
  const t = RM ? (f < 0.5 ? 0 : 1) : ease(f);
  const G = lerp(view.Gs[c], view.Gs[c + 1], t);
  const near = Math.round(P);

  // the atmosphere follows the world centre by transform, never by restyling (rule 7)
  if (els.atmo) {
    const tf = `translate3d(${W.cx.toFixed(1)}px,${W.cy.toFixed(1)}px,0) translate(-50%,-50%)`;
    if (tf !== atmoTf) {
      els.atmo.style.transform = tf;
      atmoTf = tf;
    }
  }

  if (near !== lastNear && els.whereN && els.whereT) {
    lastNear = near;
    els.whereN.textContent = String(near + 1);
    els.whereT.textContent = NAMES[near];
  }

  for (let i = 0; i < N; i++) {
    const wr = els.wrap[i];
    if (!wr) continue;
    L[c](i, A);
    L[c + 1](i, B);
    const ti = RM ? t : ease(clamp((f - DL[i]) / 0.78, 0, 1));
    const z = Math.min(lerp(A.z, B.z, ti), ZMAX),
      r = lerp(A.r, B.r, ti);
    let o = lerp(A.o, B.o, ti);
    const s = F / (F - z);
    // the readability rule lives here: nothing is ever drawn above its natural size
    const sc = Math.min(1, s * G),
      sx = W.cx + lerp(A.x, B.x, ti) * sc,
      sy = W.cy + lerp(A.y, B.y, ti) * sc;
    o *= 1 - FADE * (1 - clamp((s - 0.2) / 0.38, 0, 1)); // depth reads as falling into the dark
    const zi = 5000 + Math.round(z);

    if (o < 0.004) {
      if (vis[i]) {
        wr.style.visibility = "hidden";
        vis[i] = 0;
      }
      continue;
    }
    if (!vis[i]) {
      wr.style.visibility = "visible";
      vis[i] = 1;
    }
    // coarse step on the page (re-renders text), the remainder on the wrapper (GPU only)
    const q = Math.exp(Math.round(Math.log(Math.max(sc, 0.01)) * QSTEP) / QSTEP);
    if (q !== cQ[i]) {
      els.sc[i].style.transform = `scale(${q.toFixed(5)})`;
      cQ[i] = q;
    }
    const tf = `translate3d(${sx.toFixed(1)}px,${sy.toFixed(1)}px,0) rotate(${r.toFixed(2)}deg) scale(${(sc / q).toFixed(4)})`;
    if (tf !== cTf[i]) {
      wr.style.transform = tf;
      cTf[i] = tf;
    }
    const os = o.toFixed(2);
    if (os !== cOp[i]) {
      wr.style.opacity = os;
      cOp[i] = os;
    }
    if (zi !== cZ[i]) {
      wr.style.zIndex = String(zi);
      cZ[i] = zi;
    }
  }
}

let stopCurrent: (() => void) | null = null;

/**
 * Start the world: measure, attach every listener, run the frame loop. Idempotent:
 * while a world is running, a second call starts nothing and returns the same stop.
 * Stop cancels the loop and removes every listener; calling it twice is harmless.
 */
export function startWorld(): () => void {
  if (stopCurrent) return stopCurrent;

  const ac = new AbortController();
  const { signal } = ac;
  let raf = 0;

  resetStore();
  resetCaches();
  measure();
  ys = scrollY;

  addEventListener("resize", measure, { signal });
  const ro = new ResizeObserver(() => measure());
  ro.observe(document.body);
  document.fonts?.ready.then(() => {
    if (!signal.aborted) measure();
  });
  attachPointer(signal);

  const loop = (now: number) => {
    raf = requestAnimationFrame(loop);
    frame(now);
  };
  raf = requestAnimationFrame((now) => {
    last = now;
    loop(now);
  });

  const stop = () => {
    if (signal.aborted) return;
    cancelAnimationFrame(raf);
    ac.abort();
    ro.disconnect();
    if (stopCurrent === stop) stopCurrent = null;
  };
  stopCurrent = stop;
  return stop;
}
