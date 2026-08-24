import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { clearTokens, getAccessToken, me } from "../api/client";
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
    } catch {
      // Expired/invalid token - treat as logged out rather than surfacing
      // a raw fetch error on app load.
      clearTokens();
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
