// Actions the page triggers on the world. Each writes a target into the store;
// the frame loop eases toward it.

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
