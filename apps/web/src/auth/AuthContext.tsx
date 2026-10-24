import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ApiError,
  clearTokens,
  getAccessToken,
  logout as endSession,
  me,
  setSessionExpiredHandler,
} from "../api/client";
import type { MeResponse } from "../api/client";
import { retryTransient } from "../api/retryTransient";
import { AuthContext } from "./authContextValue";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [meResponse, setMeResponse] = useState<MeResponse | null | undefined>(undefined);

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

  // Plan item 34, option 3: revokes the session on the server, then clears
  // the tokens even if that call failed (client.ts, logout()). No screen
  // offers it yet; a Log out control is plan item 39.
  const logout = useCallback(async () => {
    await endSession();
    setMeResponse(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Phase 4 plan item 34: client.ts refreshes an expired access token on
  // its own; this runs only when that fails (the refresh token rejected, or
  // a 401 again with the new token). client.ts has already cleared the
  // tokens; nulling meResponse makes RequireAuth redirect to /login with
  // the current location as `from`, so the user returns to it.
  useEffect(() => {
    setSessionExpiredHandler(() => setMeResponse(null));
    return () => setSessionExpiredHandler(null);
  }, []);

  const organizationId = meResponse?.memberships[0]?.organization_id ?? null;

  return (
    <AuthContext.Provider value={{ meResponse, organizationId, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
