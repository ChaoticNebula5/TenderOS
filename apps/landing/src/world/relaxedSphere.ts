import { Vector3 } from "three";

/**
 * Fibonacci-lattice points on a unit sphere, relaxed by a short mutual-
 * repulsion simulation so neighbours end up roughly equidistant instead of
 * showing the lattice's spiral seams. Ported from reference-landing.html's
 * `relaxedSphere` (docs/reference-landing.html:840-852).
 *
 * Pure function of `n` - no scene state - so it can run once at module load
 * for the fixed 40-document count and be reused by every formation that
 * scatters sheets around the sphere (`sphere`, and later `redteam`,
 * `crossdoc`, `recede`).
 */
export function relaxedSphere(n: number): Vector3[] {
  const golden = Math.PI * (3 - Math.sqrt(5));
  const points: Vector3[] = [];
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    points.push(new Vector3(Math.cos(golden * i) * r, y, Math.sin(golden * i) * r));
  }

  const forces = points.map(() => new Vector3());
  const delta = new Vector3();
  const ITERATIONS = 60;
  for (let it = 0; it < ITERATIONS; it++) {
    forces.forEach((f) => f.set(0, 0, 0));
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        delta.subVectors(points[i], points[j]);
        const lengthSq = Math.max(delta.lengthSq(), 1e-6);
        delta.multiplyScalar(1 / (lengthSq * Math.sqrt(lengthSq)));
        forces[i].add(delta);
        forces[j].sub(delta);
      }
    }
    const k = (0.35 / n) * (1 - it / ITERATIONS);
    points.forEach((p, i) => p.addScaledVector(forces[i], k).normalize());
  }
  return points;
}
