// Change impact: the bid as a dependency graph from source files through tender
// requirements and proposal sections to the readiness gate.

import { D, E, N } from "./tender";

export type ImpactNode = {
  id: number; // card id
  col: number; // 0 sources, 1 requirements, 2 sections, 3 gate
  b?: string; // label lead (page number)
  label: string; // label text
  name: string; // name in the impact summary
};

export const NODES: readonly ImpactNode[] = [
  { id: E(3), col: 0, label: "ISO27001_2022.pdf", name: "ISO27001_2022.pdf" },
  { id: E(0), col: 0, label: "A_Mehta_CV.pdf", name: "A_Mehta_CV.pdf" },
  { id: E(9), col: 0, label: "RateSchedule_2026.xlsx", name: "RateSchedule_2026.xlsx" },
  { id: 19, col: 1, b: "p. 173", label: "Information security", name: "information security (p. 173)" },
  { id: 14, col: 1, b: "p. 122", label: "Key personnel", name: "key personnel (p. 122)" },
  { id: 20, col: 1, b: "p. 186", label: "Data handling", name: "data handling (p. 186)" },
  { id: 21, col: 1, b: "p. 199", label: "Schedule of rates", name: "schedule of rates (p. 199)" },
  { id: D(0), col: 2, label: "Section 4, capability", name: "Section 4" },
  { id: D(1), col: 2, label: "Section 6, delivery team", name: "Section 6" },
  { id: D(2), col: 2, label: "Section 9, pricing", name: "Section 9" },
  { id: D(3), col: 2, label: "Executive summary", name: "Executive summary" },
  { id: D(4), col: 3, label: "Readiness gate", name: "readiness gate" },
];

/** Directed edges between NODES indices: a depends-on relation pointing downstream. */
export const EDGES: readonly (readonly [number, number])[] = [
  [0, 3], [0, 5], [1, 4], [1, 5], [2, 6], [3, 7], [3, 10], [4, 7],
  [4, 8], [5, 8], [6, 9], [6, 10], [7, 11], [8, 11], [9, 11], [10, 11],
];

/** The three causes, by source node index. */
export const CAUSES: readonly { node: number; button: string; verb: string }[] = [
  { node: 0, button: "ISO 27001 certificate expires", verb: "is no longer valid" },
  { node: 1, button: "A. Mehta leaves the team", verb: "is withdrawn from the bid" },
  { node: 2, button: "Rate schedule revised", verb: "is revised" },
];

/** Index of the readiness gate in NODES. */
export const GATE = NODES.findIndex((n) => n.col === 3);
/** Hops per second: slow enough to follow. */
export const WAVE = 0.9;
/** Hop count for a node the wave never reaches. */
export const UNREACHED = 99;

/** NODES index for each card id, or -1. */
export const nodeOf: readonly number[] = (() => {
  const a = new Array<number>(N).fill(-1);
  NODES.forEach((n, j) => (a[n.id] = j));
  return a;
})();
/** How many nodes sit in each column, and each node's row within its column. */
export const colCount: readonly number[] = [0, 1, 2, 3].map((c) => NODES.filter((n) => n.col === c).length);
export const rowOf: readonly number[] = NODES.map((n, j) => NODES.slice(0, j).filter((m) => m.col === n.col).length);
