import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { login, register, setTokens } from "../api/client";
import { formatErrorDetail } from "../api/formatErrorDetail";
import { useAuth } from "../auth/useAuth";
import { Button } from "../components/ui/Button";
import { Field } from "../components/ui/Field";
import { Notice } from "../components/ui/Notice";
import "./Login.css";

// Screen 1 (Part J). Covers both login and signup - auth_service.register
// creates the organization+user+membership together (organization_name is
// a body field on /auth/register), so no separate "create organization"
// screen exists among the 17, and none is needed.
export function Login() {
  const { meResponse, refresh } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [organizationName, setOrganizationName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // RequireAuth.tsx sets this when it redirects here - the path a user was
  // actually trying to reach, whether they arrived here genuinely logged
  // out or via a transient /auth/me failure that temporarily (and
  // incorrectly, per AuthContext.tsx's own refresh()) nulled meResponse.
  // Read once, used in both branches below, so a real login and an
  // auth-recovery bounce land the user in the same place instead of two
  // different fixes for the same underlying "where was I going" question.
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  // Already logged in - don't show the login form again.
  if (meResponse) {
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "login") {
        // Deliberately not wrapped in retryTransient: a 503 LOGIN_UNAVAILABLE
        // (decision 0041) says when to try again, and its Retry-After is
        // longer than retryTransient's backoff, so an automatic retry would
        // only meet the same outage. A 429 would spend more of the login
        // limit. The user sees the wait and decides (Login.test.tsx).
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
      navigate(from, { replace: true });
    } catch (err) {
      setError(formatErrorDetail(err));
    } finally {
      setSubmitting(false);
    }
  }

  // Login sits outside the app shell (no account yet), so it brings its own
  // gutter (.page) and width (.login, --width-auth, plan item 13).
  //
  // The mode switch is two toggle buttons with aria-pressed (plan item 6),
  // replacing `disabled` as a selected-tab marker: a disabled control is
  // skipped by Tab and announced as unavailable, not as selected. They stay
  // role="button", so full-flow.spec.ts (setup's register)'s getByRole("button",
  // { name: "Create organization" }) still finds the switch, and setup's register still
  // scopes the submit to <form>. Clicking the already-pressed one sets the
  // same mode again - a no-op, as clicking a disabled one was.
  return (
    <div className="page">
      <main className="login">
        <h1 className="wordmark wordmark--title">TenderOS</h1>
        <div className="action-row login__modes">
          <Button
            variant="quiet"
            className="login__mode"
            aria-pressed={mode === "login"}
            onClick={() => setMode("login")}
          >
            Log in
          </Button>
          <Button
            variant="quiet"
            className="login__mode"
            aria-pressed={mode === "register"}
            onClick={() => setMode("register")}
          >
            Create organization
          </Button>
        </div>

        <form onSubmit={handleSubmit}>
          {/* autocomplete: "username" lets a password manager pair the
              email with the saved password on login; "email" when it is a
              new account's address. current-/new-password tells it whether
              to fill or to offer to generate and save. */}
          <Field label="Email">
            <input
              type="email"
              required
              autoComplete={mode === "login" ? "username" : "email"}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              required
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {mode === "register" && (
            <>
              <Field label="Organization name">
                <input
                  type="text"
                  required
                  autoComplete="organization"
                  value={organizationName}
                  onChange={(e) => setOrganizationName(e.target.value)}
                />
              </Field>
              <Field label="Your display name (optional)">
                <input
                  type="text"
                  autoComplete="name"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </Field>
            </>
          )}

          {error && (
            <Notice variant="failed" alert>
              {error}
            </Notice>
          )}

          <Button type="submit" variant="primary" busy={submitting} busyLabel="Please wait...">
            {mode === "login" ? "Log in" : "Create organization"}
          </Button>
        </form>
      </main>
    </div>
  );
}
