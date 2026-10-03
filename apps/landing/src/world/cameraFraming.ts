/**
 * Camera framing: distance and frustum offset derived from a formation's
 * geometry (`halfW`/`halfH`/`front`, from `formations.ts`) and the live
 * viewport (window size, header height, which side of the screen the
 * current chapter's text column occupies). Rule 4: "never a hand-tuned
 * distance." Ported from reference-landing.html:1090-1104 (`measureRegion`,
 * `distanceFor`) and 1664-1677 (how the render loop blends and applies them).
 */

export interface FrameRegion {
  /** Centre of the free (non-text-column) part of the screen, in CSS px. */
  cx: number;
  cy: number;
  /** Size of that free region. */
  w: number;
  h: number;
}

export type ChapterSide = "left" | "right" | "center";

/**
 * The rectangle of screen space not covered by the current chapter's text
 * column - this is the region the world's sheets get framed inside, not
 * the full viewport, which is why a left-side chapter's formation visibly
 * sits right of centre.
 *
 * `colWidth` mirrors the CSS custom properties `--col: min(470px,40vw)` and
 * `--col-wide: min(620px,46vw)` plus up to 52px of gutter
 * (`clamp(24px,4.5vw,64px)`'s upper bound) - kept as plain numbers here
 * rather than reading computed CSS, since this runs up to 60 times a
 * second and querying computed styles that often would be its own
 * performance problem.
 */
export function measureRegion(
  side: ChapterSide,
  viewportWidth: number,
  viewportHeight: number,
  topInset: number,
  wide: boolean,
  isMobile: boolean,
): FrameRegion {
  const colWidth = (wide ? Math.min(viewportWidth * 0.46, 620) : Math.min(viewportWidth * 0.42, 470)) + Math.min(viewportWidth * 0.04, 52);
  let cx: number;
  let w: number;
  if (isMobile || side === "center") {
    cx = viewportWidth / 2;
    w = viewportWidth;
  } else if (side === "left") {
    cx = (colWidth + viewportWidth) / 2;
    w = viewportWidth - colWidth;
  } else {
    cx = (viewportWidth - colWidth) / 2;
    w = viewportWidth - colWidth;
  }
  return { cx, w, cy: (topInset + viewportHeight) / 2, h: viewportHeight - topInset };
}

/**
 * Camera distance such that a formation's bounding half-extents just fill
 * `region` (not the full viewport) under perspective projection at
 * `fovDegrees`. `region.h`/`viewportHeight` and `region.w`/`viewportHeight`
 * are how much of the camera's angular budget the free region actually
 * gets - both measured against height because vertical FOV is the
 * camera's primitive value and horizontal FOV falls out of it via aspect,
 * so scaling both axes by the same viewport height keeps them consistent
 * with each other.
 */
export function distanceForFormation(
  formation: { halfW: number; halfH: number; front: number },
  region: FrameRegion,
  fovDegrees: number,
  viewportHeight: number,
): number {
  const tanHalfFov = Math.tan((fovDegrees * Math.PI) / 360);
  const tanV = (tanHalfFov * region.h) / viewportHeight;
  const tanH = (tanHalfFov * region.w) / viewportHeight;
  return Math.max(formation.halfH / tanV, formation.halfW / tanH) + formation.front;
}
