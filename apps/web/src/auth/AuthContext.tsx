import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, clearTokens, getAccessToken, me } from "../api/client";
import type { MeResponse } from "../api/client";
import { AuthContext } from "./authContextValue";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [meResponse, setMeResponse] = useState<MeResponse | null | undefined>(undefined);

  const refresh = useCallback(async () => {
    if (!getAccessToken()) {
      setMeResponse(null);
      return;
    }
    try {
      setMeResponse(await me());
    } catch (err) {
      // Only a real auth rejection (401/403) means the token itself is
      // bad - clear it and treat as logged out. Any other failure
      // (network drop, a 500) keeps the token, so a later retry/refresh
      // can still succeed instead of forcing a re-login for an outage
      // that had nothing to do with the session.
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        clearTokens();
      }
      setMeResponse(null);
    }
  }, []);

  const logout = useCallback(() => {
    clearTokens();
    setMeResponse(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const organizationId = meResponse?.memberships[0]?.organization_id ?? null;

  return (
    <AuthContext.Provider value={{ meResponse, organizationId, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
