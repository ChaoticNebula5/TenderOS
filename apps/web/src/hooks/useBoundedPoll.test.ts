import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  POLL_FIRST_DELAY_MS,
  POLL_IDLE_LIMIT_MS,
  POLL_MAX_DELAY_MS,
  useBoundedPoll,
} from "./useBoundedPoll";

interface Props {
  active: boolean;
  fingerprint: string;
}

let visibility: DocumentVisibilityState = "visible";

function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  document.dispatchEvent(new Event("visibilitychange"));
}

function renderPoll(read: () => Promise<void>, initial: Props) {
  return renderHook(({ active, fingerprint }: Props) => useBoundedPoll(read, active, fingerprint), {
    initialProps: initial,
  });
}

// Advances fake time by `ms` and returns how many reads happened in it. In
// slices of at most 1 s, each in its own act(): React applies a read's
// state update, which schedules the next timer, only when act() ends, so
// one long advance would fire one read and then find no timer left.
async function readsDuring(read: ReturnType<typeof vi.fn>, ms: number): Promise<number> {
  const before = read.mock.calls.length;
  for (let left = ms; left > 0; left -= 1_000) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(Math.min(left, 1_000));
    });
  }
  return read.mock.calls.length - before;
}

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useBoundedPoll", () => {
  it("reads after 2 s, then twice as long each time, never more than 30 s apart", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    renderPoll(read, { active: true, fingerprint: "a" });

    expect(await readsDuring(read, POLL_FIRST_DELAY_MS - 1)).toBe(0);
    expect(await readsDuring(read, 1)).toBe(1); // 2 s
    expect(await readsDuring(read, 4_000 - 1)).toBe(0);
    expect(await readsDuring(read, 1)).toBe(1); // 4 s later
    expect(await readsDuring(read, 8_000)).toBe(1);
    expect(await readsDuring(read, 16_000)).toBe(1);
    // 32 s would be next; the ceiling holds it at 30 s, and there it stays.
    expect(await readsDuring(read, POLL_MAX_DELAY_MS - 1)).toBe(0);
    expect(await readsDuring(read, 1)).toBe(1);
    expect(await readsDuring(read, POLL_MAX_DELAY_MS)).toBe(1);
  });

  it("goes back to 2 s when what the page shows changes", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderPoll(read, { active: true, fingerprint: "doc:uploaded" });
    await readsDuring(read, 2_000 + 4_000 + 8_000); // three reads; next due in 16 s

    rerender({ active: true, fingerprint: "doc:parsing" });

    expect(await readsDuring(read, POLL_FIRST_DELAY_MS - 1)).toBe(0);
    expect(await readsDuring(read, 1)).toBe(1);
    expect(await readsDuring(read, 4_000)).toBe(1);
  });

  it("does not read while nothing is unfinished, and stops once nothing is", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderPoll(read, { active: false, fingerprint: "doc:parsed" });
    expect(await readsDuring(read, 60_000)).toBe(0);

    rerender({ active: true, fingerprint: "doc:uploaded" });
    expect(await readsDuring(read, POLL_FIRST_DELAY_MS)).toBe(1);

    rerender({ active: false, fingerprint: "doc:parsed" });
    expect(await readsDuring(read, 10 * 60_000)).toBe(0);
  });

  it("does not read while the tab is hidden, and starts afresh when it is shown", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    renderPoll(read, { active: true, fingerprint: "a" });
    await readsDuring(read, 2_000 + 4_000 + 8_000); // backed off to 16 s

    act(() => setVisibility("hidden"));
    expect(await readsDuring(read, 20 * 60_000)).toBe(0);

    act(() => setVisibility("visible"));
    // From the first delay, and the hidden 20 minutes did not use up the
    // 15-minute idle limit.
    expect(await readsDuring(read, POLL_FIRST_DELAY_MS)).toBe(1);
    expect(await readsDuring(read, 4_000)).toBe(1);
  });

  it("stops after 15 minutes with no change and says so; Check again reads at once and restarts", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    const { result } = renderPoll(read, { active: true, fingerprint: "stuck:parsing" });

    await readsDuring(read, POLL_IDLE_LIMIT_MS - 1);
    expect(result.current.stopped).toBe(false);
    await readsDuring(read, POLL_MAX_DELAY_MS); // the next due read finds the limit passed
    expect(result.current.stopped).toBe(true);
    expect(await readsDuring(read, 60 * 60_000)).toBe(0);

    const before = read.mock.calls.length;
    act(() => result.current.checkAgain());
    expect(read.mock.calls.length).toBe(before + 1);
    expect(result.current.stopped).toBe(false);
    expect(await readsDuring(read, POLL_FIRST_DELAY_MS)).toBe(1);
  });

  it("starts watching again by itself when something changes after it stopped", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderPoll(read, { active: true, fingerprint: "one" });
    await readsDuring(read, POLL_IDLE_LIMIT_MS + POLL_MAX_DELAY_MS);
    expect(result.current.stopped).toBe(true);

    rerender({ active: true, fingerprint: "one,two" }); // e.g. another upload
    expect(result.current.stopped).toBe(false);
    expect(await readsDuring(read, POLL_FIRST_DELAY_MS)).toBe(1);
  });

  it("reports nothing stopped once nothing is unfinished", async () => {
    const read = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderPoll(read, { active: true, fingerprint: "x" });
    await readsDuring(read, POLL_IDLE_LIMIT_MS + POLL_MAX_DELAY_MS);
    expect(result.current.stopped).toBe(true);

    rerender({ active: false, fingerprint: "x" });
    expect(result.current.stopped).toBe(false);
  });

  it("carries on after a failed read", async () => {
    const read = vi.fn().mockRejectedValueOnce(new Error("503")).mockResolvedValue(undefined);
    renderPoll(read, { active: true, fingerprint: "a" });
    expect(await readsDuring(read, 2_000)).toBe(1);
    expect(await readsDuring(read, 4_000)).toBe(1);
  });
});
