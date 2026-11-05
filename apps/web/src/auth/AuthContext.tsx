import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ApiError,
  clearTokens,
  getAccessToken,
  isLoggingOut,
  logout as endSession,
  me,
  setSessionExpiredHandler,
} from "../api/client";
import type { MeResponse } from "../api/client";
import { retryTransient } from "../api/retryTransient";
import { AuthContext } from "./authContextValue";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [meResponse, setMeResponse] = useState<MeResponse | null | undefined>(undefined);
  const [loggedOut, setLoggedOut] = useState(false);

  const refresh = useCallback(async () => {
    if (!getAccessToken()) {
      setMeResponse(null);
      return;
    }
    try {
      // retryTransient absorbs a network blip or a brief backend restart
      // (not a 429: Phase 4 blocker 1 made rate limits terminal, see
      // retryTransient.ts) before this falls through to "treat as logged
      // out" below - previously ANY me() failure nulled meResponse on the
      // first try (KNOWN_LIMITATIONS.md, "[B16, owner B17] Login.tsx's
      // 'already logged in' guard drops the user's original destination"),
      // which is what made a single transient failure look identical to a
      // real session rejection to every consumer of meResponse.
      setMeResponse(await retryTransient(() => me()));
      setLoggedOut(false);
    } catch (err) {
      // Only a real auth rejection (401/403) means the token itself is
      // bad - clear it and treat as logged out. Any other failure
      // (network drop, a 500), now only reached after retryTransient's
      // own retries are exhausted, keeps the token, so a later retry/
      // refresh can still succeed instead of forcing a re-login for an
      // outage that had nothing to do with the session.
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        clearTokens();
      }
      setMeResponse(null);
    }
  }, []);

  // Plan item 34, option 3: clears the tokens, then revokes the session on the
  // server, whether or not that call works (client.ts, logout()). The top bar's
  // Log out button calls it (plan item 39).
  //
  // Tokens in storage after the wait mean a poll landed the user on /login
  // during it and they signed in again: that new session is theirs to keep.
  const logout = useCallback(async () => {
    await endSession();
    if (getAccessToken()) return;
    setLoggedOut(true);
    setMeResponse(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Phase 4 plan item 34: client.ts refreshes an expired access token on
  // its own; this runs only when that fails (the refresh token rejected, or
  // a 401 again with the new token). client.ts has already cleared the
  // tokens; nulling meResponse makes RequireAuth redirect to /login with
  // the current location as `from`, so the user returns to it. Not while
  // logout() runs: a screen polling during its wait lands here too, and that
  // is a deliberate log out, with no page to return to (plan item 39).
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setLoggedOut(isLoggingOut());
      setMeResponse(null);
    });
    return () => setSessionExpiredHandler(null);
  }, []);

  const organizationId = meResponse?.memberships[0]?.organization_id ?? null;

  return (
    <AuthContext.Provider value={{ meResponse, organizationId, refresh, logout, loggedOut }}>
      {children}
    </AuthContext.Provider>
  );
}
