// World rectangle, scroll holds and per-arrangement framing. Recomputed on load,
// resize, body size change and font load; never per frame.

import { N } from "../data/tender";
import { CH, CW, F, clamp } from "./math";
import { L, NC, ZMAX, type Pose } from "./arrangements";
import { CT, ctx, els } from "./store";

type Rect = { x: number; y: number; w: number; h: number; cx: number; cy: number };
const rect = (): Rect => ({ x: 0, y: 0, w: 1, h: 1, cx: 0, cy: 0 });

/** Live world rect (W), and its two resting places: right of the text (W0), left of it (W1). */
export const W = rect();
export const W0 = rect();
export const W1 = rect();

export const view = {
  vw: 1,
  vh: 1,
  /** [start, end] scroll position over which each chapter's arrangement holds still. */
  holds: [] as [number, number][],
  /** Fit factor per arrangement, so its projected bounds fill the world rect. */
  Gs: new Array<number>(NC).fill(1),
};

/** The reference's world top edge. The header itself ends at about 57 px without insets. */
const HEAD = 60;
const tmp: Pose = { x: 0, y: 0, z: 0, r: 0, o: 0 };

export function measure() {
  const vw = innerWidth,
    vh = innerHeight;
  view.vw = vw;
  view.vh = vh;
  ctx.portrait = vw <= 760;
  // the world starts below the header: the reference's 60 px, or the header's measured bottom
  // edge when it is taller (a top safe-area inset on a notched phone pads the header down)
  const head = Math.max(HEAD, document.querySelector("header")?.getBoundingClientRect().bottom ?? 0);

  // the world rect: beside the text column on desktop, the top of the screen on a phone
  if (ctx.portrait) {
    Object.assign(W0, { x: 0, y: head, w: vw, h: vh * 0.56 - head });
    Object.assign(W1, W0);
  } else {
    const gx = clamp(vw * 0.042, 20, 64),
      colw = Math.min(470, vw * 0.36),
      ww = vw - (gx + colw + 28);
    Object.assign(W0, { x: vw - ww, y: head, w: ww, h: vh - head });
    Object.assign(W1, { x: 0, y: head, w: ww, h: vh - head });
  }
  Object.assign(W, W0);
  W.cx = W.x + W.w / 2;
  W.cy = W.y + W.h / 2;

  // scroll holds: a pinned chapter holds for its whole pin, others around their centre
  const secs = [...document.querySelectorAll<HTMLElement>("[data-ch]")];
  view.holds = secs.map((sec): [number, number] => {
    const r = sec.getBoundingClientRect(),
      top = r.top + scrollY,
      h = r.height;
    const st = sec.querySelector(".stick");
    if (st && getComputedStyle(st).position === "sticky") return [top, top + h - vh];
    const c = top + h / 2 - vh / 2,
      hw = vh * (+(sec.dataset.hold ?? "") || 0.12);
    return [c - hw, c + hw];
  });
  if (view.holds.length !== NC) return; // chapters not mounted: nothing to map scroll onto
  view.holds[0] = [-Infinity, Math.max(view.holds[0][1], vh * 0.1)];
  view.holds[NC - 1][1] = Infinity;
  ctx.y1 = view.holds[1][0] / vh;

  // framing from geometry and the live aspect: fit each arrangement's projected bounds
  const save = { ...ctx };
  Object.assign(ctx, { mx: 0, my: 0, T: 0, dyaw: 0, dpitch: 0, hoff: 0 });
  for (let k = 0; k < NC; k++) {
    ctx.yv = (isFinite(view.holds[k][0]) ? view.holds[k][0] : 0) / vh;
    let bw = 1,
      bh = 1;
    for (let i = 0; i < N; i++) {
      L[k](i, tmp);
      if (tmp.o < 0.5) continue;
      const s = F / (F - Math.min(tmp.z, ZMAX));
      bw = Math.max(bw, Math.abs(tmp.x * s) + (CW / 2) * s);
      bh = Math.max(bh, Math.abs(tmp.y * s) + (CH / 2) * s);
    }
    // k === 1 is the helix, which may run off the top and bottom
    const lw = ctx.portrait ? 0.94 : k === 1 ? 0.72 : 0.86,
      lh = k === 1 ? 99 : 0.84;
    view.Gs[k] = Math.min(1, (W.w * lw) / (2 * bw), (W.h * lh) / (2 * bh));
  }
  Object.assign(ctx, save);
  els.wires?.setAttribute("viewBox", `0 0 ${vw} ${vh}`);

  // where each document's cited line sits, so the evidence thread lands on the exact line
  for (let i = 0; i < N; i++) {
    const ct = els.card[i]?.querySelector<HTMLElement>(".ct");
    CT[i] = ct ? ct.offsetTop + ct.offsetHeight / 2 - CH / 2 : 0;
  }
}

/** Scroll position to chapter progress: k inside chapter k's hold, k + f between holds k and k + 1. */
export function pOf(y: number) {
  const holds = view.holds;
  if (holds.length !== NC) return 0;
  for (let i = 0; i < NC; i++) {
    const [s, e] = holds[i];
    if (y <= e) {
      if (i === 0 || y >= s) return i;
      const pe = holds[i - 1][1];
      return i - 1 + clamp((y - pe) / (s - pe), 0, 1);
    }
  }
  return NC - 1;
}
