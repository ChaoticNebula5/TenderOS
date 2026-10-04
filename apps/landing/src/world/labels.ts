// Labels drawn beside documents, outside their transforms so they never scale.
// Defined here as data; Labels.tsx renders them once; placeLabels() moves them each frame.

import { NODES } from "../data/impact";
import { EVROW, MAND, PAGES, REQ, SHORT, ST } from "../data/tender";
import { CH, CW, clamp } from "./math";
import { W } from "./layout";
import { ctx, scr } from "./store";

/** 'out': beside the page, on the side away from the world centre. 't': above. 'b': below. */
type Side = "out" | "t" | "b";

type LabelDef = {
  ch: number; // chapter the label belongs to
  id: number; // card id it follows
  side: Side;
  cls: string;
  ww: number; // width in px at scale 1, 0 for nowrap
  b?: string; // bold lead
  span?: string; // second line (stacked labels)
  text?: string; // plain text after the lead
  em?: string; // status line
};

export const LABELS: readonly LabelDef[] = [
  ...MAND.map((i): LabelDef => ({ ch: 1, id: i, side: "out", cls: "", ww: 0, b: `p. ${PAGES[i][0]}`, text: PAGES[i][3] })),
  ...REQ.map((r): LabelDef => ({
    ch: 2, id: r.pg, side: "t", cls: `${r.st} ctr stack`, ww: 420,
    b: `p. ${PAGES[r.pg][0]}`, span: PAGES[r.pg][3], em: ST[r.st],
  })),
  ...EVROW.map((id): LabelDef => ({ ch: 2, id, side: "b", cls: "ctr", ww: 350, text: SHORT[id] })),
  ...NODES.map((n): LabelDef => ({ ch: 4, id: n.id, side: "b", cls: "ctr", ww: 0, b: n.b, text: n.label })),
];

/** Label elements, by LABELS index. Filled by Labels.tsx. */
export const labelEl: HTMLDivElement[] = [];

// change-only caches, by LABELS index
const vis = new Uint8Array(LABELS.length);
const on = new Uint8Array(LABELS.length);
const tfC: string[] = new Array(LABELS.length).fill("");
const opC: string[] = new Array(LABELS.length).fill("");
const wdC = new Int32Array(LABELS.length);

export function resetLabels() {
  vis.fill(0);
  on.fill(0);
  tfC.fill("");
  opC.fill("");
  wdC.fill(0);
  for (const el of labelEl) {
    if (!el) continue;
    el.style.visibility = "hidden";
    el.classList.remove("on", "hit");
  }
}

/**
 * Place every label from this frame's document positions. `weight(ch)` is how present
 * chapter ch is (1 inside its hold, falling to 0 a little way out); `hot` is the
 * highlighted helix page; `reached(id)` says whether the impact wave has reached a
 * document. At phone width documents are too small to label.
 */
export function placeLabels(weight: (ch: number) => number, hot: number, reached: (id: number) => boolean) {
  for (let k = 0; k < LABELS.length; k++) {
    const lb = LABELS[k],
      el = labelEl[k];
    if (!el) continue;
    const i = lb.id;
    // a class flag per label: `on` for the highlighted helix page, `hit` once the impact wave arrives
    if (lb.ch === 1 || lb.ch === 4) {
      const h = (lb.ch === 1 ? i === hot : reached(i)) ? 1 : 0;
      if (h !== on[k]) {
        el.classList.toggle(lb.ch === 1 ? "on" : "hit", h === 1);
        on[k] = h;
      }
    }
    const a = ctx.portrait ? 0 : weight(lb.ch) * clamp((scr.o[i] - 0.25) / 0.45, 0, 1);
    if (a < 0.01) {
      if (vis[k]) {
        el.style.visibility = "hidden";
        vis[k] = 0;
        tfC[k] = "";
      }
      continue;
    }
    if (!vis[k]) {
      el.style.visibility = "visible";
      vis[k] = 1;
    }
    const s = scr.s[i];
    let x: number,
      y: number,
      tx = "-50%",
      ty = "0";
    if (lb.side === "b") {
      x = scr.x[i];
      y = scr.y[i] + (CH * s) / 2 + 7;
    } else if (lb.side === "t") {
      x = scr.x[i];
      y = scr.y[i] - (CH * s) / 2 - 7;
      ty = "-100%";
    } else {
      const left = scr.x[i] < W.cx;
      x = scr.x[i] + (left ? -1 : 1) * ((CW * s) / 2 + 10);
      y = scr.y[i];
      tx = left ? "-100%" : "0";
      ty = "-50%";
    }
    if (lb.ww) {
      const wd = Math.max(60, Math.round(lb.ww * s - 6));
      if (wd !== wdC[k]) {
        el.style.width = wd + "px";
        wdC[k] = wd;
      }
    }
    const tf = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px) translate(${tx},${ty})`;
    if (tf !== tfC[k]) {
      el.style.transform = tf;
      tfC[k] = tf;
    }
    const as = a.toFixed(2);
    if (as !== opC[k]) {
      el.style.opacity = as;
      opC[k] = as;
    }
  }
}
