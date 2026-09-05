import { describe, expect, it } from "vitest";

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
