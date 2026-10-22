import { describe, expect, it } from "vitest";

import { ApiError, parseRetryAfter } from "./client";
import { formatErrorDetail } from "./formatErrorDetail";

// Phase 4 blocker 1. formatErrorDetail had no test file of its own. These
// cover the two shapes it now reads first (a 429, the API's error
// envelope) and pin the older behaviour they sit in front of, so a later
// change to the new branches cannot quietly break the 422 path.

describe("formatErrorDetail", () => {
  it("shows the message from the API's error envelope", () => {
    const err = new ApiError(401, {
      error: { code: "INVALID_CREDENTIALS", message: "Incorrect email or password." },
    });
    expect(formatErrorDetail(err)).toBe("Incorrect email or password.");
  });

  it("shows the catch-all 500's generic message, not a status code", () => {
    const err = new ApiError(500, {
      error: {
        code: "INTERNAL_ERROR",
        message: "Something went wrong on our side. Please try again.",
      },
    });
    expect(formatErrorDetail(err)).toBe("Something went wrong on our side. Please try again.");
  });

  it("falls back to the status when the envelope's message is missing or empty", () => {
    expect(formatErrorDetail(new ApiError(409, { error: { code: "CONFLICT" } }))).toBe(
      "Request failed (409)",
    );
    expect(formatErrorDetail(new ApiError(409, { error: { message: "  " } }))).toBe(
      "Request failed (409)",
    );
  });

  it("a 429 with a readable Retry-After shows the wait in seconds", () => {
    const err = new ApiError(
      429,
      { error: { code: "RATE_LIMITED", message: "Too many login attempts." } },
      42,
    );
    expect(formatErrorDetail(err)).toBe("Too many attempts. Please wait 42 seconds and try again.");
  });

  it("a 429 without a readable Retry-After still says what happened", () => {
    const err = new ApiError(429, {
      error: { code: "RATE_LIMITED", message: "Too many requests." },
    });
    expect(formatErrorDetail(err)).toBe("Too many attempts. Please wait a moment and try again.");
  });

  it("still joins FastAPI's 422 detail array", () => {
    const err = new ApiError(422, {
      detail: [{ msg: "field required" }, { msg: "value is not a valid email" }],
    });
    expect(formatErrorDetail(err)).toBe("field required; value is not a valid email");
  });

  it("still treats a non-ApiError as a network failure", () => {
    expect(formatErrorDetail(new TypeError("Failed to fetch"))).toBe(
      "Could not reach the server. Please try again.",
    );
  });
});

describe("parseRetryAfter", () => {
  it.each([
    ["42", 42],
    [" 7 ", 7],
    ["0", 0],
  ])("reads %j as %d seconds", (value, expected) => {
    expect(parseRetryAfter(value)).toBe(expected);
  });

  it.each([null, "", "soon", "-5", "1.5", "Wed, 21 Oct 2026 07:28:00 GMT"])(
    "reads %j as unreadable",
    (value) => {
      expect(parseRetryAfter(value)).toBeNull();
    },
  );
});
