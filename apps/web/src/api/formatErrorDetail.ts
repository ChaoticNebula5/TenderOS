import { ApiError } from "./client";

// Shared by every screen that renders a raw ApiError from a form submit
// (CreateBid, Login - more will likely follow as more forms get built).
// FastAPI's own 422 response shape puts `detail` as an array of
// {msg, loc, ...} objects, not a string - String(detail) on that array
// renders the literal text "[object Object]" instead of anything a user
// can act on. Extracted once rather than left duplicated per screen,
// since both copies would otherwise need this same fix applied twice
// (and re-applied to the next screen that adds its own copy).
export function formatErrorDetail(err: unknown): string {
  if (!(err instanceof ApiError)) {
    return "Could not reach the server. Please try again.";
  }
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
