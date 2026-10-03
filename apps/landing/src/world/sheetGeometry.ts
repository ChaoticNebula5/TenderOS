import { Float32BufferAttribute, PlaneGeometry } from "three";

/** Sheet width in world units. Everything else in the world (sphere radius,
 *  helix radius/rise, camera framing halfW/halfH) is derived from this. */
export const SHEET_WIDTH = 2.6;
export const SHEET_HEIGHT = SHEET_WIDTH * 1.36;

/**
 * The helix cylinder's radius, angular step and per-document rise, for a
 * given document count - derived, not typed, same as everything else rule 4
 * governs. Ported from reference-landing.html:804-810. `helixRadius` alone
 * was needed from P1 onward to build the sheet's morph target; `step` and
 * `rise` are P2's addition, read by `world/formations.ts`'s `helix`
 * formation to place each sheet around the cylinder - kept in one function
 * since all three come from the same three constants (SHEET_WIDTH,
 * documentCount, and the derived turns count).
 */
export function helixLayout(documentCount: number): { turns: number; step: number; helixRadius: number; rise: number } {
  const TAU = Math.PI * 2;
  const turns = Math.max(1, Math.round(documentCount / 12));
  const step = (TAU * turns) / documentCount;
  const helixRadius = (SHEET_WIDTH * 1.3) / step;
  const rise = (1.266 * helixRadius * turns) / documentCount;
  return { turns, step, helixRadius, rise };
}

/**
 * One flat document sheet, plus a morph target that bends it onto the helix
 * cylinder wall computed by `helixLayout`. Ported from
 * reference-landing.html:856-864.
 *
 * @param helixRadius from `helixLayout(documentCount).helixRadius`
 * @param segmentsX horizontal subdivisions - the reference drops this from
 *   18 to 8 on mobile to cut vertex count; kept as a parameter for the same
 *   reason rather than hard-coded.
 */
export function buildSheetGeometry(helixRadius: number, segmentsX = 18): PlaneGeometry {
  const geometry = new PlaneGeometry(SHEET_WIDTH, SHEET_HEIGHT, segmentsX, 1);
  const position = geometry.attributes.position;
  const count = position.count;

  const bentPosition = new Float32Array(count * 3);
  const bentNormal = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const theta = position.getX(i) / helixRadius;
    bentPosition[i * 3] = Math.sin(theta) * helixRadius;
    bentPosition[i * 3 + 1] = position.getY(i);
    bentPosition[i * 3 + 2] = Math.cos(theta) * helixRadius - helixRadius;
    bentNormal[i * 3] = Math.sin(theta);
    bentNormal[i * 3 + 2] = Math.cos(theta);
  }

  geometry.morphAttributes.position = [new Float32BufferAttribute(bentPosition, 3)];
  geometry.morphAttributes.normal = [new Float32BufferAttribute(bentNormal, 3)];
  return geometry;
}
