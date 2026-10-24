import { useMemo, useRef } from "react";

// Phase 4 (upload-list race). A screen that reads a whole list (on mount,
// or again after an operation finishes) and also changes single rows in
// place (an upload, a resolve, a save) can have a read in flight while a
// change lands. The read's answer describes the server before the change,
// and replacing the list with it undoes the change on screen: CI run
// 39983426118 lost an uploaded document that way and the E2E failed at
// full-flow.spec.ts:540.
//
// One clock orders both kinds of event. A read records when it started; a
// change records when it landed, per row. When a read answers:
//   - if a read that started later has already been applied, this one is
//     an older picture of the whole list and is dropped;
//   - otherwise its rows replace the list by id, except rows changed
//     after it started, which keep their local version (and are kept even
//     when the read does not have them yet, like a fresh upload).
// Merging by id instead of dropping the stale read keeps everything else
// it carries: after a re-validation, the new findings arrive together with
// the one resolved meanwhile.

export interface Identified {
  id: string;
}

// Pure, so it is tested on its own (useListReads.test.ts).
export function mergeListRead<T extends Identified>(
  fresh: T[],
  current: T[],
  startedAt: number,
  changedAt: ReadonlyMap<string, number>,
): T[] {
  const changedSince = (id: string) => (changedAt.get(id) ?? 0) > startedAt;
  const currentById = new Map(current.map((row) => [row.id, row]));
  const merged = fresh.map((row) =>
    changedSince(row.id) ? (currentById.get(row.id) ?? row) : row,
  );
  const inFresh = new Set(fresh.map((row) => row.id));
  for (const row of current) {
    if (!inFresh.has(row.id) && changedSince(row.id)) merged.push(row);
  }
  return merged;
}

export interface ListReads {
  // Call as a whole-list read is sent; give the result to merge().
  start(): number;
  // Call when a local change to one row has landed.
  changed(id: string): void;
  // The list to show for a read that started at startedAt, or `current`
  // unchanged if a later read has already been applied. Pure enough for a
  // setState updater, which StrictMode runs twice: a second call with the
  // same arguments returns the same list.
  merge<T extends Identified>(startedAt: number, fresh: T[], current: T[]): T[];
}

export function useListReads(): ListReads {
  const clock = useRef(0);
  const changedAt = useRef(new Map<string, number>());
  const lastApplied = useRef(0);

  return useMemo(
    () => ({
      start: () => ++clock.current,
      changed: (id: string) => {
        changedAt.current.set(id, ++clock.current);
      },
      merge: <T extends Identified>(startedAt: number, fresh: T[], current: T[]) => {
        if (startedAt < lastApplied.current) return current;
        lastApplied.current = startedAt;
        return mergeListRead(fresh, current, startedAt, changedAt.current);
      },
    }),
    [],
  );
}
