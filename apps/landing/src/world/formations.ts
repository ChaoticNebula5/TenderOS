import { Euler, Quaternion, Vector3 } from "three";
import { KB, MANDATORY_STACK_POSITIONS } from "../data/kb";
import { SHEET_HEIGHT, SHEET_WIDTH, helixLayout } from "./sheetGeometry";
import { relaxedSphere } from "./relaxedSphere";
import { clamp01, ease } from "./mathUtils";

/**
 * Formations: pure functions that place one sheet for one instant in time.
 * Spec §4: "(doc, out, t) => void writing position, quaternion, bend, scale,
 * opacity, emissive, plus a static {halfW, halfH, front} used for framing."
 * Ported from reference-landing.html:887-1008 (world setup) and 911-933
 * (the `sphere` and `pack` entries themselves - P1's scope; the other seven
 * formations named in the reference belong to P2-P5).
 *
 * One structural change from the reference: `sphere.f` and `pack.f` there
 * close over module-level globals (`hand`, `packOpen`, `hotClause`) mutated
 * by DOM event handlers elsewhere in the file. A "pure function" that reads
 * hidden mutable globals isn't actually pure - and two <World> instances on
 * the same page would fight over the same globals. Here that per-frame input
 * is an explicit fourth argument instead, so the function's only inputs are
 * its arguments.
 */

const N = KB.length;
export const SHEET_RADIUS_SPHERE = SHEET_HEIGHT * 3.75;

const X_AXIS = new Vector3(1, 0, 0);
const Y_AXIS = new Vector3(0, 1, 0);
const FLAT_QUATERNION = new Quaternion().setFromAxisAngle(X_AXIS, -Math.PI / 2);
const IDENTITY_QUATERNION = new Quaternion();

/** Unit sphere points (radius 1), relaxed once for the fixed 40-document
 *  count and scaled by whichever formation needs a sphere layout. */
const SPHERE_POINTS = relaxedSphere(N);

/** Helix cylinder layout, derived once for the fixed 40-document count -
 *  same values `sheetGeometry.ts` used to build the morph target, read
 *  again here to place sheets around that same cylinder. */
const HELIX = helixLayout(N);

export interface SheetUnit {
  /** Index into the KB array / sphere point set, 0..N-1. */
  index: number;
  /** Random per-sheet phase offset so bobbing sheets don't move in lockstep. */
  phase: number;
  /** -1 if this document doesn't ground a drafted claim. */
  claimIndex: number;
  /**
   * "Has this document been scrolled past yet" and "is this document the
   * current lens" - both in [0,1], both smoothed toward a target once per
   * frame in Scene.tsx's useFrame (reference: `u.rd`/`u.lens`,
   * reference-landing.html:1699/1706), then read here as already-current
   * values. Mutated in place frame to frame rather than reallocated, same
   * reasoning as the scratch objects below - these two fields are the one
   * place `SheetUnit` stops being "properties of the document" and starts
   * carrying per-frame state, which is why they default to 0 here rather
   * than being computed at the `KB.map` callsite like `phase`/`claimIndex`.
   */
  rd: number;
  lens: number;
}

export interface FormationOutput {
  position: Vector3;
  quaternion: Quaternion;
  bend: number;
  scale: number;
  opacity: number;
  emissive: number;
}

export function createFormationOutput(): FormationOutput {
  return { position: new Vector3(), quaternion: new Quaternion(), bend: 0, scale: 1, opacity: 1, emissive: 0 };
}

/** Everything a formation needs beyond "which sheet, what instant" - drag
 *  angles, pack open/hover state, and now the reading pin's progress plus
 *  the world-space point the current lens document flies to. Owned by hooks
 *  or the depth-calculation TODO outside the formation layer, read here,
 *  never written. */
export interface FormationInput {
  heroYaw: number;
  heroPitch: number;
  packOpen: number;
  hotClauseIndex: number;
  /** Raw [0,1] progress through the reading chapter's pinned scroll. */
  readP: number;
  /** `readP` rescaled to "which sheet index is at the front of the
   *  helix," 0..N-1 - reference's `readTravel`. */
  readTravel: number;
  /**
   * World-space point the lens document flies to while being read.
   * TODO(human): this is a placeholder (world origin) until the depth
   * calculation is written - see Scene.tsx's TODO for the real value and
   * why it can't be computed yet. Sheets will fly toward the wrong point
   * once `lens` ramps above 0 until that lands; `lens` itself is never
   * driven above 0 in P2 as shipped here for exactly that reason (see
   * Scene.tsx), so in practice this placeholder is never read from a
   * position where it would visibly matter yet.
   */
  pageAnchor: Vector3;
}

export interface Formation {
  halfW: number;
  halfH: number;
  front: number;
  f(unit: SheetUnit, out: FormationOutput, elapsedSeconds: number, input: FormationInput): void;
}

// Scratch objects reused across calls (one call per sheet per frame, 40
// sheets) rather than allocated fresh each time - the same pattern the
// reference uses for its own per-frame temp vectors.
const scratchPosition = new Vector3();
const scratchQuaternion = new Quaternion();
const scratchEuler = new Euler();

export const FORMATIONS: Record<"sphere" | "pack" | "helix", Formation> = {
  sphere: {
    halfW: SHEET_RADIUS_SPHERE + SHEET_HEIGHT * 0.6,
    halfH: SHEET_RADIUS_SPHERE + SHEET_HEIGHT * 0.6,
    front: SHEET_RADIUS_SPHERE,
    f(unit, out, t, input) {
      const angle = t * 0.055 + input.heroYaw;
      const p = SPHERE_POINTS[unit.index];
      out.position
        .set(p.x * Math.cos(angle) + p.z * Math.sin(angle), p.y, -p.x * Math.sin(angle) + p.z * Math.cos(angle))
        .multiplyScalar(SHEET_RADIUS_SPHERE);
      out.position.applyAxisAngle(X_AXIS, input.heroPitch);
      out.position.y += Math.sin(t * 0.4 + unit.phase) * 0.2;
      out.quaternion.setFromAxisAngle(X_AXIS, input.heroPitch * 0.5);
      out.bend = 0;
      out.scale = 1;
      out.opacity = 0.92;
      out.emissive = unit.claimIndex >= 0 ? 0.34 : 0;
    },
  },

  pack: {
    halfW: 10,
    halfH: 13,
    front: 9,
    f(unit, out, t, input) {
      const k = unit.index - (N - 1) / 2;
      const mandatoryRank = MANDATORY_STACK_POSITIONS.indexOf(unit.index as (typeof MANDATORY_STACK_POSITIONS)[number]);
      const open = input.packOpen;

      scratchPosition.set(mandatoryRank >= 0 ? 1.2 + open * 6.4 : 0, k * (0.3 + open * 0.12), 0);
      scratchQuaternion.setFromAxisAngle(Y_AXIS, 0.42 + t * 0.015);
      scratchPosition.applyQuaternion(scratchQuaternion);

      scratchEuler.set(0.5, 0, 0);
      out.quaternion.setFromEuler(scratchEuler);
      scratchPosition.applyQuaternion(out.quaternion);
      out.position.copy(scratchPosition);
      out.quaternion.multiply(scratchQuaternion).multiply(FLAT_QUATERNION);

      out.bend = 0;
      out.scale = 1.55;
      out.opacity = mandatoryRank >= 0 ? 1 : 0.8;
      out.emissive =
        mandatoryRank >= 0 ? (input.hotClauseIndex === mandatoryRank ? 1 : 0.3 + open * 0.35) : 0;
    },
  },

  helix: {
    halfW: HELIX.helixRadius + SHEET_WIDTH,
    halfH: (N * HELIX.rise) / 2 + SHEET_HEIGHT,
    front: HELIX.helixRadius,
    // Unlike sphere/pack, helix has no continuous ambient motion - position
    // is entirely scroll-driven (input.readP/readTravel), matching the
    // reference's own helix.f exactly (elapsedSeconds is an unused
    // parameter there too). Prefixed `_` so noUnusedParameters doesn't flag
    // an intentional, reference-faithful non-use.
    f(unit, out, _t, input) {
      // How far this sheet has wrapped onto the cylinder, staggered by
      // index so the wrap-in reads as a wave rather than everything
      // snapping at once. This is the value rule 2 and spec §9's
      // transparency-plus-morph-target warning are both about: it drives
      // `bend`, which becomes `morphTargetInfluences[0]` in Scene.tsx.
      const wrapAmount = ease(clamp01((input.readP - 0.01 - (unit.index / N) * 0.06) / 0.12));

      const k = unit.index + input.readTravel;
      const angle = (k - N / 2) * HELIX.step;
      const wrapped = (((k % N) + N) % N) - N / 2;

      out.position.copy(SPHERE_POINTS[unit.index]).multiplyScalar(SHEET_RADIUS_SPHERE);
      scratchPosition.set(Math.sin(angle) * HELIX.helixRadius, -wrapped * HELIX.rise, Math.cos(angle) * HELIX.helixRadius);
      out.position.lerp(scratchPosition, wrapAmount);

      out.quaternion.setFromAxisAngle(Y_AXIS, angle).slerp(IDENTITY_QUATERNION, 1 - wrapAmount);
      out.bend = wrapAmount;
      out.scale = 1;

      // Sheets near the "seam" (the helix's open ends) fade out rather
      // than pop, and everything dims slightly once it's been read or is
      // the current lens - `Math.max(unit.rd, unit.lens)` because either
      // is reason enough to recede.
      const edgeDistance = Math.abs(wrapped + 0.5) / (N / 2);
      const edgeFade = edgeDistance > 0.8 ? clamp01(1 - (edgeDistance - 0.8) / 0.2) : 1;
      const fade = 1 - (1 - edgeFade) * wrapAmount;
      out.opacity = fade * (0.94 - 0.5 * wrapAmount * (1 - Math.max(unit.rd, unit.lens)));
      out.emissive = unit.lens * 0.14;

      if (unit.lens > 0.001) {
        // The read document flies from the cylinder wall to the page
        // position - see FormationInput.pageAnchor's own note on why this
        // branch is wired but currently inert in P2 as shipped.
        out.position.lerp(input.pageAnchor, unit.lens);
        out.quaternion.slerp(IDENTITY_QUATERNION, unit.lens);
        out.bend *= 1 - unit.lens;
        out.opacity = Math.max(out.opacity, unit.lens);
      }
    },
  },
};

/**
 * Chapters 3 onward (comply, draft, red, impact, price, close) name a
 * formation P3-P5 haven't built yet. Rather than let a lookup miss crash the
 * frame, the world holds on "helix" - the last implemented formation - so
 * scrolling past the reading chapter leaves the sheets settled and still,
 * instead of undefined. Updated from P1's "hold on pack" now that helix
 * exists; same reasoning, later fallback.
 */
export function resolveFormation(name: string): Formation {
  if (name === "sphere") return FORMATIONS.sphere;
  if (name === "pack") return FORMATIONS.pack;
  return FORMATIONS.helix;
}
