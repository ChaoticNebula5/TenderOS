// Actions the page triggers on the world. Each writes a target into the store;
// the frame loop eases toward it.

import { CAUSES, EDGES, NODES, UNREACHED } from "../data/impact";
import { HN, helixBase } from "./arrangements";
import { input } from "./store";

/**
 * Spin the helix so tender page i faces the reader at mid-height. In the helix a page
 * sits square to the reader when i + helixBase() + offset is 12 (mod 24); of all the
 * offsets that do that, take the one nearest the current target so the helix turns
 * the short way round instead of unwinding.
 */
export function bringToFront(i: number) {
  let off = HN / 2 - i - helixBase();
  off += Math.round((input.hoffT - off) / HN) * HN;
  input.hoffT = off;
}

/** Evidence tracing: select statement i. The thread redraws from its start. */
export function selectClaim(i: number) {
  input.sel = i;
  input.threadP = 0;
}

/**
 * Change impact: start the wave from cause k. Hops are found breadth first over the
 * real dependency edges, once, here; the frame loop then advances the wave and reads
 * the hops from the store. Returns the hops so the page can write its summary.
 */
export function seedImpact(k: number): number[] {
  const s = CAUSES[k].node;
  const next: number[][] = NODES.map(() => []);
  for (const [a, b] of EDGES) next[a].push(b);
  const hop = NODES.map(() => UNREACHED);
  hop[s] = 0;
  const queue = [s];
  for (let q = 0; q < queue.length; q++) {
    const a = queue[q];
    for (const b of next[a])
      if (hop[b] === UNREACHED) {
        hop[b] = hop[a] + 1;
        queue.push(b);
      }
  }
  input.cause = k;
  input.causeNode = s;
  input.hop = hop;
  input.wave = 0;
  return hop;
}

/** Evidence tracing: clear the selection. The cited document returns to the wall. */
export function clearClaims() {
  input.sel = -1;
  input.threadP = 0;
}
