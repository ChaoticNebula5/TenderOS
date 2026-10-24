import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { mergeListRead, useListReads } from "./useListReads";

interface Row {
  id: string;
  state: string;
}

const row = (id: string, state: string): Row => ({ id, state });

describe("mergeListRead", () => {
  it("replaces the list with the read when nothing changed since it started", () => {
    const merged = mergeListRead(
      [row("a", "server"), row("b", "server")],
      [row("a", "old"), row("c", "gone on the server")],
      5,
      new Map(),
    );
    expect(merged).toEqual([row("a", "server"), row("b", "server")]);
  });

  it("keeps a row changed after the read started, in the read's order", () => {
    const merged = mergeListRead(
      [row("a", "unresolved"), row("b", "new finding")],
      [row("a", "resolved")],
      5,
      new Map([["a", 6]]),
    );
    expect(merged).toEqual([row("a", "resolved"), row("b", "new finding")]);
  });

  it("keeps a row added after the read started even though the read lacks it", () => {
    const merged = mergeListRead([], [row("upload", "pending")], 5, new Map([["upload", 6]]));
    expect(merged).toEqual([row("upload", "pending")]);
  });

  it("takes the read's version of a row changed before it started", () => {
    const merged = mergeListRead(
      [row("a", "parsed")],
      [row("a", "pending")],
      5,
      new Map([["a", 4]]),
    );
    expect(merged).toEqual([row("a", "parsed")]);
  });
});

describe("useListReads", () => {
  it("drops a read older than one already applied, and stays the same across a second call", () => {
    const { result } = renderHook(() => useListReads());
    const reads = result.current;
    const older = reads.start();
    const newer = reads.start();

    const afterNewer = reads.merge(newer, [row("a", "parsed")], []);
    expect(afterNewer).toEqual([row("a", "parsed")]);
    // StrictMode runs a setState updater twice.
    expect(reads.merge(newer, [row("a", "parsed")], [])).toEqual([row("a", "parsed")]);

    expect(reads.merge(older, [row("a", "pending")], afterNewer)).toBe(afterNewer);
  });

  it("a change between start and merge keeps the local row", () => {
    const { result } = renderHook(() => useListReads());
    const reads = result.current;
    const startedAt = reads.start();
    reads.changed("a");

    expect(reads.merge(startedAt, [row("a", "unresolved")], [row("a", "resolved")])).toEqual([
      row("a", "resolved"),
    ]);
  });

  it("is the same object across renders, so effects that depend on it do not rerun", () => {
    const { result, rerender } = renderHook(() => useListReads());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
