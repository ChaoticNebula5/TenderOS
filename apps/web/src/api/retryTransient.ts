import { ApiError } from "./client";

// B17 Phase 1. Shared by AuthContext.tsx's refresh() and GateDecisionPanel.
// tsx's handleDecide() - see KNOWN_LIMITATIONS.md, "Standing pattern: this
// frontend has no recovery path from a single transient request failure."
// Both call sites already detect and report their own failure correctly;
// neither distinguishes "this session/decision is genuinely rejected" from
// "one request happened to fail," and neither retries - a real transient
// blip (a rate limit, a network hiccup, a brief backend restart) strands
// the user with no way back except a manual retry.
//
// useEventStream.ts (B14/B15) already solved the identical classification
// problem for its own stream reconnects - the same terminal-vs-transient
// split, the same bounded exponential backoff shape - but inline, specific
// to EventSource. This extracts that policy into a plain async wrapper any
// caller can use around ANY promise-returning call, not just a stream.
//
// Deliberately a plain function, not a React hook: AuthContext.refresh()
// runs inside a context provider's useCallback, not a component's own
// render cycle, and GateDecisionPanel.handleDecide() is an imperative click
// handler - neither shape fits a hook's rules-of-hooks constraints as
// naturally as a function every async call site can just `await`.
//
// Separate file from client.ts, not folded in: client.ts's own docstring
// scopes it to "one function per Part G route" - a route-mirroring module
// with no opinion on retry policy. retryTransient has no knowledge of
// routes or response schemas at all; it operates on `() => Promise<T>`
// generically. Keeping it here keeps client.ts's contract clean and makes
// this trivially unit-testable in isolation, with a fake fn and no fetch
// mocking required.

export interface RetryTransientOptions {
  maxAttempts?: number;
  onRetry?: (attempt: number, delayMs: number, error: unknown) => void;
}

const DEFAULT_MAX_ATTEMPTS = 3;
const MAX_BACKOFF_MS = 10_000;

// An ALLOWLIST of retryable statuses, not a denylist of terminal ones - a
// denylist means every status nobody has thought about yet defaults to
// "retry it," which is exactly the "safety net attached to a fallback"
// shape this project's own KNOWN_LIMITATIONS.md standing pattern warns
// about: the next terminal status anyone adds (400, 404, 422, ...) would
// have silently gotten three rounds of backoff before surfacing an error
// that was never going to change, and nobody would notice until it shipped.
// 408/429/5xx are the actual transient set - a timeout, a rate limit, or the
// server's own overload/unavailability, all real candidates to succeed on a
// later attempt. Everything else, ApiError or not (a raw network failure -
// fetch itself throwing - IS retried; anything that reached a real HTTP
// response with a status outside this set is not), is terminal.
const RETRYABLE_STATUSES = new Set([408, 429, 500, 502, 503, 504]);

function isRetryable(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true; // a real network/transport failure
  return RETRYABLE_STATUSES.has(err.status);
}

// Bounded exponential backoff, the same `Math.min(1000 * 2 ** n,
// MAX_BACKOFF_MS)` shape useEventStream.ts already uses for its own stream
// reconnects, applied only to the retryable set above.
export async function retryTransient<T>(
  fn: () => Promise<T>,
  options: RetryTransientOptions = {},
): Promise<T> {
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  // A caller-supplied Infinity/NaN would make `attempt >= maxAttempts` never
  // become true, so a permanently-failing operation retries forever instead
  // of surfacing an error - in GateDecisionPanel that strands the UI in
  // "submitting" indefinitely rather than showing the user a failure.
  if (!Number.isInteger(maxAttempts) || maxAttempts < 1) {
    throw new RangeError("maxAttempts must be a positive integer");
  }
  for (let attempt = 1; ; attempt++) {
    try {
      return await fn();
    } catch (err) {
      if (!isRetryable(err) || attempt >= maxAttempts) {
        throw err;
      }
      const delayMs = Math.min(1000 * 2 ** (attempt - 1), MAX_BACKOFF_MS);
      options.onRetry?.(attempt, delayMs, err);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
}
