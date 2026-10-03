// The evidence thread: from the live position of the selected statement to the exact
// line it cites, or to a dead end when nothing supports it. Rendered once by Wires.tsx;
// placeThread() writes geometry, dash and opacity, each only when it changes.

import { CLAIMS } from "../data/claims";
import { CW } from "./math";
import { W, view } from "./layout";
import { CT, scr } from "./store";
import { cubicLen, type Pt } from "./wires";

type El<T> = { el: T | null };
export const thread: El<SVGPathElement> = { el: null };
export const threadGlow: El<SVGPathElement> = { el: null };
export const tend: El<SVGCircleElement> = { el: null };

// change-only caches
let dC = "",
  glowC = "",
  dashC = "",
  offC = "",
  opC = "",
  endOpC = "",
  endPosC = "",
  colourC = "";

export function resetThread() {
  dC = glowC = dashC = offC = opC = endOpC = endPosC = colourC = "";
  thread.el?.removeAttribute("d");
  threadGlow.el?.removeAttribute("d");
  for (const e of [thread.el, threadGlow.el, tend.el]) if (e) e.style.opacity = "0";
}

const f1 = (v: number) => v.toFixed(1);

function hide() {
  if (dC !== "") {
    thread.el?.setAttribute("d", "");
    dC = "";
  }
  if (glowC !== "") {
    threadGlow.el?.setAttribute("d", "");
    glowC = "";
  }
  if (endOpC !== "0") {
    if (tend.el) tend.el.style.opacity = "0";
    endOpC = "0";
  }
}

/**
 * Place the thread for statement `sel`. `rc` is the statement's rect, read once at the
 * top of the frame; `at` is the thread's opacity (evidence chapter weight times
 * selection weight); `p` is how much of it is drawn; `dir` is +1 when the world is
 * right of the text, -1 when it is left of it.
 */
export function placeThread(sel: number, rc: DOMRect | null, at: number, p: number, dir: number) {
  if (!thread.el || !threadGlow.el || !tend.el) return;
  if (sel < 0 || at < 0.02 || !rc) return hide();

  const cl = CLAIMS[sel],
    ok = "src" in cl;
  // colour by verdict: supported amber, flagged commitment slate, unsupported alarm
  const colour = ok ? "var(--amber)" : cl.kind === "flag" ? "var(--slate)" : "var(--alarm)";
  if (colour !== colourC) {
    thread.el.style.stroke = colour;
    threadGlow.el.style.stroke = colour;
    tend.el.style.stroke = colour;
    colourC = colour;
  }

  // leave the statement on the side facing the world
  const a: Pt = { x: dir > 0 ? rc.right + 8 : rc.left - 8, y: rc.top + rc.height / 2 };
  let b: Pt, c1: Pt, c2: Pt;
  if (ok) {
    // land on the document's near edge, at the height of the cited line
    const s = cl.src;
    b = { x: scr.x[s] - dir * ((CW * scr.s[s]) / 2 + 3), y: scr.y[s] + CT[s] * scr.s[s] };
    c1 = { x: a.x + (b.x - a.x) * 0.45, y: a.y - 60 };
    c2 = { x: a.x + (b.x - a.x) * 0.7, y: b.y };
  } else {
    // a short thread that drops into empty space and stops: nothing to land on
    b = { x: a.x + dir * Math.min(150, W.w * 0.18), y: Math.min(a.y + 190, view.vh - 70) };
    c1 = { x: a.x + dir * 90, y: a.y - 24 };
    c2 = { x: b.x + dir * 24, y: b.y - 120 };
  }
  const d = `M${f1(a.x)} ${f1(a.y)}C${f1(c1.x)} ${f1(c1.y)} ${f1(c2.x)} ${f1(c2.y)} ${f1(b.x)} ${f1(b.y)}`;

  if (d !== dC) {
    thread.el.setAttribute("d", d);
    dC = d;
    const g = ok ? d : "";
    if (g !== glowC) {
      threadGlow.el.setAttribute("d", g);
      glowC = g;
    }
  }
  // the dash is one path length long, padded so the computed length always covers the real one
  const len = cubicLen(a, c1, c2, b) * 1.002 + 1;
  const dash = f1(len);
  if (dash !== dashC) {
    thread.el.style.strokeDasharray = `${dash} ${dash}`;
    threadGlow.el.style.strokeDasharray = `${dash} ${dash}`;
    dashC = dash;
  }
  const off = f1(len * (1 - p));
  if (off !== offC) {
    thread.el.style.strokeDashoffset = off;
    threadGlow.el.style.strokeDashoffset = off;
    offC = off;
  }
  const op = at.toFixed(2);
  if (op !== opC) {
    thread.el.style.opacity = op;
    threadGlow.el.style.opacity = op;
    opC = op;
  }
  // the end ring marks a dead end, once the thread has fully drawn
  const endOp = !ok && p > 0.98 ? op : "0";
  if (endOp !== endOpC) {
    tend.el.style.opacity = endOp;
    endOpC = endOp;
  }
  const pos = `${f1(b.x)} ${f1(b.y)}`;
  if (pos !== endPosC) {
    tend.el.setAttribute("cx", f1(b.x));
    tend.el.setAttribute("cy", f1(b.y));
    endPosC = pos;
  }
}
