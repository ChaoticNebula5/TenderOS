import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./client";
import { retryTransient } from "./retryTransient";

// CodeRabbit-caught gap: retryTransient had no dedicated test file at all -
// only indirect coverage via GateDecisionPanel.test.tsx/AuthContext's own
// real call sites, which exercise the terminal/retryable split but never a
// caller-supplied maxAttempts. This is that direct test, scoped to the one
// new behavior: an invalid maxAttempts (Infinity, NaN, 0, a fraction) must
// fail fast rather than silently retry forever, which would strand a caller
// like GateDecisionPanel in "submitting" indefinitely.

describe("retryTransient's maxAttempts validation", () => {
  it.each([Infinity, NaN, 0, -1, 1.5])(
    "rejects a maxAttempts of %s before ever calling fn",
    async (maxAttempts) => {
      let calls = 0;
      await expect(
        retryTransient(
          async () => {
            calls++;
            return "unreachable";
          },
          { maxAttempts },
        ),
      ).rejects.toThrow(RangeError);
      expect(calls).toBe(0);
    },
  );

  it("still allows a normal positive integer maxAttempts through unchanged", async () => {
    let calls = 0;
    const result = await retryTransient(
      async () => {
        calls++;
        return "ok";
      },
      { maxAttempts: 2 },
    );
    expect(result).toBe("ok");
    expect(calls).toBe(1);
  });
});

// Phase 4 blocker 1: a 429 is terminal. The API's limit windows are 60 s, so
// a retry a second or two later almost never succeeds and only uses up more
// of the user's attempts; the caller shows the wait instead.
describe("retryTransient and rate limits", () => {
  it("never retries a 429, even with attempts to spare", async () => {
    let calls = 0;
    const rateLimited = new ApiError(429, { error: { code: "RATE_LIMITED" } }, 30);
    await expect(
      retryTransient(
        async () => {
          calls++;
          throw rateLimited;
        },
        { maxAttempts: 3 },
      ),
    ).rejects.toBe(rateLimited);
    expect(calls).toBe(1);
  });

  it("still retries a 503, so the 429 test above is not passing by accident", async () => {
    vi.useFakeTimers();
    try {
      let calls = 0;
      const pending = retryTransient(
        async () => {
          calls++;
          if (calls === 1) throw new ApiError(503, {});
          return "ok";
        },
        { maxAttempts: 2 },
      );
      await vi.runAllTimersAsync();
      await expect(pending).resolves.toBe("ok");
      expect(calls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
