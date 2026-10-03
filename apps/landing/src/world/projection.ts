import { Raycaster, Vector2, Vector3, type Camera, type Group } from "three";
import { SHEET_HEIGHT, SHEET_WIDTH } from "./sheetGeometry";

export interface ScreenPoint {
  x: number;
  y: number;
  /** NDC depth from `Vector3.project` - callers treat `z > 1` as "behind
   *  the camera," matching the reference's own `boxOf` guard. */
  z: number;
}

export interface ScreenBox {
  x: number;
  y: number;
  /** Half-width/half-height in screen pixels. */
  hw: number;
  hh: number;
}

const scratchProjected = new Vector3();

/**
 * Projects a world-space point to page pixel coordinates, wherever the
 * world canvas currently sits on the page. `canvasOrigin`/`canvasSize` are
 * parameters rather than assumed globals - in this app's single "world"
 * mode the canvas always fills the viewport (origin 0,0, size = window),
 * but threading them explicitly matches how the rest of world/ passes
 * per-frame state rather than reaching for module globals (formations.ts's
 * own note on `FormationInput` explains the same choice). Ported from
 * reference-landing.html:1137-1139 (`screenOf`).
 */
export function screenOf(worldPoint: Vector3, camera: Camera, canvasOriginX: number, canvasOriginY: number, canvasWidth: number, canvasHeight: number): ScreenPoint {
  scratchProjected.copy(worldPoint).project(camera);
  return {
    x: canvasOriginX + (scratchProjected.x * 0.5 + 0.5) * canvasWidth,
    y: canvasOriginY + (-scratchProjected.y * 0.5 + 0.5) * canvasHeight,
    z: scratchProjected.z,
  };
}

const raycaster = new Raycaster();
const ndc = new Vector2();

/**
 * The inverse of `screenOf` at a fixed depth: given a page pixel and a
 * distance along the camera's forward axis, returns the world-space point
 * there. Ported from reference-landing.html:1146-1148 (`pointAt`) - casts a
 * ray from the camera through that screen pixel, then walks it out until
 * its local Z reaches `-depth` (camera-space forward is -Z, hence the
 * negation).
 */
export function pointAt(screenX: number, screenY: number, depth: number, camera: Camera, canvasOriginX: number, canvasOriginY: number, canvasWidth: number, canvasHeight: number): Vector3 {
  ndc.set(((screenX - canvasOriginX) / canvasWidth) * 2 - 1, -(((screenY - canvasOriginY) / canvasHeight) * 2 - 1));
  raycaster.setFromCamera(ndc, camera);
  return raycaster.ray.direction.clone().multiplyScalar(depth / -raycaster.ray.direction.z).add(camera.position);
}

/**
 * TODO(human): the projection helper that maps one sheet to its on-screen
 * bounding box - the piece every connector line and the reticle need to
 * know "where is this sheet, right now, on screen."
 *
 * Steps, in order:
 * 1. Project the sheet's own position (its center) to screen coordinates
 *    with `screenOf`.
 * 2. If that projected point's `z` is greater than 1, the sheet is behind
 *    the camera - return `null` (there's nothing on screen to draw a line
 *    or reticle to).
 * 3. Compute a second world-space point: the sheet's center, moved along
 *    `camRight` (the camera's own world-space right axis, not the sheet's
 *    local X) by `(SHEET_WIDTH / 2) * sheet.scale.x`.
 * 4. Project that second point to screen coordinates too.
 * 5. Compute a third world-space point: the sheet's center, moved along
 *    `camUp` (the camera's world-space up axis) by
 *    `(SHEET_HEIGHT / 2) * sheet.scale.x`.
 * 6. Project that third point to screen coordinates too.
 * 7. The box's half-width is the absolute difference between step 4's
 *    projected x and step 1's projected x.
 * 8. The box's half-height is the absolute difference between step 1's
 *    projected y and step 6's projected y.
 * 9. Return `{ x: <step 1 x>, y: <step 1 y>, hw: <step 7>, hh: <step 8> }`.
 *
 * Already in scope / available to use:
 * - `screenOf` (above, in this file) - takes a world point + camera +
 *   canvas origin/size, returns `{x, y, z}` in page pixels.
 * - `sheet: Group` - the parameter you're given; `.position` is its
 *   center, `.scale.x` is its current uniform scale (same value on x/y/z).
 * - `camRight`/`camUp: Vector3` - passed in as parameters too; Scene.tsx
 *   computes these each frame from `camera.matrixWorld`'s columns 0 and 1
 *   (right after `camera.updateMatrixWorld()`, which already runs there).
 * - `SHEET_WIDTH`/`SHEET_HEIGHT` - imported at the top of this file.
 * - `camera`, `canvasOriginX/Y`, `canvasWidthPx/HeightPx` - the same
 *   viewport/canvas values `screenOf` itself takes.
 *
 * The edge case that makes this worth writing by hand: a naive bounding
 * box would project the sheet's own local X/Y axes, or just use its
 * unrotated width/height - both break as soon as the sheet is rotated
 * (which it always is, mid-blend between formations, or curled onto the
 * helix). Offsetting along the *camera's* right/up instead of the sheet's
 * own local axes is what makes the resulting box match what a viewer
 * actually sees on screen regardless of how the sheet is oriented - that's
 * a real projective-geometry choice, not boilerplate.
 */
export function boxOf(
  sheet: Group,
  camera: Camera,
  camRight: Vector3,
  camUp: Vector3,
  canvasOriginX: number,
  canvasOriginY: number,
  canvasWidthPx: number,
  canvasHeightPx: number,
): ScreenBox | null {
  void sheet;
  void camera;
  void camRight;
  void camUp;
  void canvasOriginX;
  void canvasOriginY;
  void canvasWidthPx;
  void canvasHeightPx;
  void SHEET_WIDTH;
  void SHEET_HEIGHT;
  throw new Error("boxOf: TODO(human) - see the numbered steps in this file's comment above.");
}
