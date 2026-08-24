import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError, login, register, setTokens } from "../api/client";
import { useAuth } from "../auth/useAuth";

// Screen 1 (Part J). Covers both login and signup - auth_service.register
// creates the organization+user+membership together (organization_name is
// a body field on /auth/register), so no separate "create organization"
// screen exists among the 17, and none is needed.
export function Login() {
  const { meResponse, refresh } = useAuth();
  const navigate = useNavigate();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Already logged in - don't show the login form again.
  if (meResponse) {
    return <Navigate to="/" replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") {
        const tokens = await login({ email, password });
        setTokens(tokens.access_token, tokens.refresh_token);
      } else {
        const result = await register({
          email,
          password,
          organization_name: organizationName,
          display_name: displayName || null,
        });
        setTokens(result.access_token, result.refresh_token);
      }
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        const detail =
          typeof err.body === "object" && err.body !== null && "detail" in err.body
            ? String((err.body as { detail: unknown }).detail)
            : `Request failed (${err.status})`;
        setError(detail);
      } else {
        setError("Could not reach the server. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main style={{ maxWidth: 360, margin: "var(--space-8) auto" }}>
      <h1>TenderOS</h1>
      <div style={{ display: "flex", gap: "var(--space-3)", marginBottom: "var(--space-4)" }}>
        <button type="button" disabled={mode === "login"} onClick={() => setMode("login")}>
          Log in
        </button>
        <button type="button" disabled={mode === "register"} onClick={() => setMode("register")}>
          Create organization
        </button>
      </div>

      <form onSubmit={handleSubmit}>
        <label>
          Email
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {mode === "register" && (
          <>
            <label>
              Organization name
              <input
                type="text"
                required
                value={organizationName}
                onChange={(e) => setOrganizationName(e.target.value)}
              />
            </label>
            <label>
              Your display name (optional)
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </label>
          </>
        )}

        {error && (
          <p role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        )}

        <button type="submit" disabled={submitting}>
          {submitting ? "Please wait..." : mode === "login" ? "Log in" : "Create organization"}
        </button>
      </form>
    </main>
  );
}
