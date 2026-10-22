import { ApiError } from "./client";

// Shared by every screen that renders a raw ApiError from a form submit
// (CreateBid, Login - more will likely follow as more forms get built).
// FastAPI's own 422 response shape puts `detail` as an array of
// {msg, loc, ...} objects, not a string - String(detail) on that array
// renders the literal text "[object Object]" instead of anything a user
// can act on. Extracted once rather than left duplicated per screen,
// since both copies would otherwise need this same fix applied twice
// (and re-applied to the next screen that adds its own copy).
//
// Phase 4 blocker 1: two more shapes, checked before `detail`.
// - A 429 gets its own message, with the wait in seconds when the response's
//   Retry-After was readable (ApiError.retryAfterSeconds). Before the API's
//   CORS fix a 429 never reached this function at all: the browser hid it,
//   and the user saw "Could not reach the server".
// - The API's own errors (every APIError, the 429, the 413 and the catch-all
//   500) use the envelope {"error": {"code", "message"}}, not `detail`.
//   This function used to read only `detail`, so every one of them rendered
//   as "Request failed (NNN)". The envelope's message is written for users
//   (errors.py) and the 500's is deliberately generic, so it is shown as is.
export function formatErrorDetail(err: unknown): string {
  if (!(err instanceof ApiError)) {
    return "Could not reach the server. Please try again.";
  }
  if (err.status === 429) {
    return err.retryAfterSeconds !== null
      ? `Too many attempts. Please wait ${err.retryAfterSeconds} seconds and try again.`
      : "Too many attempts. Please wait a moment and try again.";
  }
  const envelopeMessage = readEnvelopeMessage(err.body);
  if (envelopeMessage !== null) return envelopeMessage;
  const raw =
    typeof err.body === "object" && err.body !== null && "detail" in err.body
      ? (err.body as { detail: unknown }).detail
      : null;
  if (typeof raw === "string") return raw;
  if (Array.isArray(raw)) {
    return raw
      .map((item) =>
        typeof item === "object" && item !== null && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : JSON.stringify(item),
      )
      .join("; ");
  }
  return `Request failed (${err.status})`;
}

// The message from {"error": {"message": "..."}}, or null for any other
// shape, including an envelope whose message is missing, empty or not a
// string.
function readEnvelopeMessage(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("error" in body)) return null;
  const error = (body as { error: unknown }).error;
  if (typeof error !== "object" || error === null || !("message" in error)) return null;
  const message = (error as { message: unknown }).message;
  return typeof message === "string" && message.trim() !== "" ? message : null;
}
