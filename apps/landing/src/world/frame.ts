// The one frame loop (architecture rule 2) and the world's lifecycle.

import { MAND, N, REQ, isM } from "../data/tender";
import { NAMES, SIDE } from "../data/chapters";
import { DL, L, NC, ZMAX, type Pose } from "./arrangements";
import { placeLabels, resetLabels } from "./labels";
import { W, W0, W1, measure, pOf, view } from "./layout";
import { attachPointer } from "./manipulation";
import { F, RM, clamp, ease, lerp } from "./math";
import { HF, ctx, drag, els, input, resetStore, rv, scr } from "./store";
import { placeWires, resetWires } from "./wires";

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
const state: string[] = new Array(N).fill("");
let atmoTf = "";
let lastNear = -1;
let lastSw = -1;
let hotPrev = -1;
let idxOn = -1;

let last = 0;
let ys = 0;

function resetCaches() {
  cTf.fill("");
  cOp.fill("");
  cZ.fill(0);
  cQ.fill(0);
  vis.fill(0);
  state.fill("");
  atmoTf = "";
  lastNear = -1;
  lastSw = -1;
  hotPrev = -1;
  idxOn = -1;
  for (const w of els.wrap) {
    if (!w) continue;
    delete w.dataset.s;
    w.classList.remove("hot");
  }
  for (const b of els.idxBtn) b?.classList.remove("on");
}

/** The state ring a document wears in the nearest chapter: lit, part, gap, or none. */
function desired(i: number, near: number): string {
  if (near === 1) return isM[i] ? "lit" : "";
  if (near === 2) {
    const r = REQ.find((q) => q.pg === i);
    return r ? (r.st === "met" ? "lit" : r.st) : "";
  }
  return "";
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
  ctx.hoff += (input.hoffT - ctx.hoff) * (RM ? 1 : 1 - Math.exp(-dt * 3.5));
  ctx.yv = ys / view.vh;
  const FADE = document.documentElement.dataset.theme === "light" ? 0.4 : 0.72;

  const P = pOf(ys);
  input.curP = P;
  const c = Math.min(Math.floor(P), NC - 2),
    f = P - c;
  const t = RM ? (f < 0.5 ? 0 : 1) : ease(f);
  const G = lerp(view.Gs[c], view.Gs[c + 1], t);
  /** How present chapter k is: 1 inside its hold, 0 from 0.4 of a transition away. */
  const cw = (k: number) => clamp(1 - Math.abs(P - k) * 2.5, 0, 1);
  const near = Math.round(P);

  // the world slides to the opposite side of the text as chapters alternate
  const sw = ctx.portrait ? 0 : lerp(SIDE[c], SIDE[c + 1], t);
  W.x = lerp(W0.x, W1.x, sw);
  W.y = W0.y;
  W.w = W0.w;
  W.h = W0.h;
  W.cx = W.x + W.w / 2;
  W.cy = W.y + W.h / 2;
  // the atmosphere follows the world centre by transform, never by restyling (rule 7)
  if (els.atmo) {
    const tf = `translate3d(${W.cx.toFixed(1)}px,${W.cy.toFixed(1)}px,0) translate(-50%,-50%)`;
    if (tf !== atmoTf) {
      els.atmo.style.transform = tf;
      atmoTf = tf;
    }
  }
  if (Math.abs(sw - lastSw) > 0.002 && els.scrimL && els.scrimR) {
    els.scrimL.style.opacity = (1 - sw).toFixed(3);
    els.scrimR.style.opacity = sw.toFixed(3);
    lastSw = sw;
  }

  if (near !== lastNear && els.whereN && els.whereT) {
    lastNear = near;
    els.whereN.textContent = String(near + 1);
    els.whereT.textContent = NAMES[near];
  }

  // the highlighted helix page: the hovered index row's page, else the hovered page
  const hotPg = near === 1 ? (input.idxHover >= 0 ? MAND[input.idxHover] : input.hoverCard) : -1;

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

    scr.x[i] = sx;
    scr.y[i] = sy;
    scr.s[i] = sc;
    scr.o[i] = o;
    scr.r[i] = r;
    scr.z[i] = zi;

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
    // state rings switch instantly; only the glow layers fade (rule 6)
    const ds = desired(i, near);
    if (ds !== state[i]) {
      state[i] = ds;
      wr.dataset.s = ds;
    }
  }
  if (hotPg !== hotPrev) {
    if (hotPrev >= 0) els.wrap[hotPrev]?.classList.remove("hot");
    if (hotPg >= 0) els.wrap[hotPg]?.classList.add("hot");
    hotPrev = hotPg;
  }

  // tender analysis index: the highlighted page's row, otherwise whichever page faces the reader
  let row = hotPg >= 0 ? MAND.indexOf(hotPg) : -1;
  if (row < 0 && near === 1) {
    let bf = 0.86;
    MAND.forEach((i, j) => {
      if (HF[i] > bf) {
        bf = HF[i];
        row = j;
      }
    });
  }
  if (row !== idxOn) {
    if (idxOn >= 0) els.idxBtn[idxOn]?.classList.remove("on");
    if (row >= 0) els.idxBtn[row]?.classList.add("on");
    idxOn = row;
  }

  placeLabels(cw, hotPg);
  placeWires(cw(2));
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
  resetLabels();
  resetWires();
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
