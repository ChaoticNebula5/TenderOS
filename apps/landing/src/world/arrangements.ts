// Each arrangement is a pure function of a card's id that writes a pose: position
// in world px (origin at the centre of the world area, z toward the viewer),
// rotation in degrees and opacity. Projection to the screen happens in the frame.

import { N, NE, NP, isDr } from "../data/tender";
import { F, RM, clamp, hash } from "./math";
import { ctx } from "./store";

export type Pose = { x: number; y: number; z: number; r: number; o: number };
export type Arrangement = (i: number, o: Pose) => void;

/** Points spread evenly on a unit sphere: golden-angle seed, then relaxed by mutual repulsion. */
function sphere(n: number): [number, number, number][] {
  const g = Math.PI * (3 - Math.sqrt(5));
  const p = Array.from({ length: n }, (_, i): [number, number, number] => {
    const y = 1 - (i / (n - 1)) * 2,
      r = Math.sqrt(1 - y * y);
    return [Math.cos(g * i) * r, y, Math.sin(g * i) * r];
  });
  for (let it = 0; it < 70; it++) {
    const f = p.map((): [number, number, number] => [0, 0, 0]);
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const dx = p[i][0] - p[j][0],
          dy = p[i][1] - p[j][1],
          dz = p[i][2] - p[j][2];
        const d2 = dx * dx + dy * dy + dz * dz || 1e-6,
          d = Math.sqrt(d2),
          k = 1 / d2 / d;
        f[i][0] += dx * k;
        f[i][1] += dy * k;
        f[i][2] += dz * k;
        f[j][0] -= dx * k;
        f[j][1] -= dy * k;
        f[j][2] -= dz * k;
      }
    const step = (0.35 / n) * (1 - it / 70);
    for (let i = 0; i < n; i++) {
      for (let a = 0; a < 3; a++) p[i][a] += f[i][a] * step;
      const L = Math.hypot(p[i][0], p[i][1], p[i][2]) || 1;
      p[i][0] /= L;
      p[i][1] /= L;
      p[i][2] /= L;
    }
  }
  return p;
}

const SP = sphere(NP + NE);
/** Which sphere point each document takes: shuffled so neighbours on the sphere are unrelated. */
const slot = Array.from({ length: NP + NE }, (_, i) => i).sort((a, b) => hash(a * 3.1) - hash(b * 3.1));

const R = 900; // sphere radius
const ZS = -1850; // camera sits 3.28 radii from the sphere centre, as measured on the reference

/** Rotate unit vector u by yaw (about y) then pitch (about x). */
function rot(u: readonly number[], yaw: number, pitch: number, o: Pose) {
  const cy = Math.cos(yaw),
    sy = Math.sin(yaw),
    cp = Math.cos(pitch),
    sp = Math.sin(pitch);
  const x = u[0] * cy + u[2] * sy,
    z0 = -u[0] * sy + u[2] * cy;
  o.x = x;
  o.y = u[1] * cp - z0 * sp;
  o.z = u[1] * sp + z0 * cp;
}

function hidden(o: Pose) {
  o.x = 0;
  o.y = 0;
  o.z = -4400;
  o.r = 0;
  o.o = 0;
}

/** Far, dim shell: the documents not in play right now. Drafts are not shown at all. */
const park: Arrangement = (i, o) => {
  if (isDr(i)) return hidden(o);
  rot(SP[slot[i]], ctx.T * 0.025 + 1.3, -0.1, o);
  o.x *= R * 1.75;
  o.y *= R * 1.3;
  o.z = o.z * R * 0.8 - 3700;
  o.r = (hash(i) - 0.5) * 10;
  o.o = 0.16;
};

/** Overview: every tender page and evidence file on a slowly turning sphere. */
const pile: Arrangement = (i, o) => {
  if (isDr(i)) return hidden(o);
  const yaw = (RM ? 0 : ctx.T * 0.07) + ctx.yv * 0.9 + ctx.mx * 0.22 + ctx.dyaw;
  const pitch = clamp(-0.19 + ctx.my * 0.08 + ctx.dpitch, -1.25, 1.25);
  rot(SP[slot[i]], yaw, pitch, o);
  o.x *= R;
  o.y *= R;
  o.z = o.z * R + ZS;
  o.r = (hash(i) - 0.5) * 14;
  o.o = 1;
};

/**
 * One arrangement per chapter, indexed by data-ch. Chapters whose arrangement is
 * not built yet hold their documents in the park shell: helix (V2), match (V2),
 * proof (V3), impact (V4), submission (V5) replace these entries.
 */
export const L: readonly Arrangement[] = [pile, park, park, park, park, park];
export const NC = L.length;

/** Per-document delay in every morph, so documents move as a loose crowd. */
export const DL = Array.from({ length: N }, (_, i) => hash(i * 1.7 + 0.3) * 0.22);

/** z is clamped here before projection, so a document never passes the camera. */
export const ZMAX = F * 0.55;
