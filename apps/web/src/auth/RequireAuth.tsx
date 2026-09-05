import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { meResponse } = useAuth();
  const location = useLocation();

  if (meResponse === undefined) {
    return <p>Loading...</p>;
  }
  if (meResponse === null) {
    // Carries the path this redirect interrupted so Login.tsx can send the
    // user back to it once authenticated, instead of always landing on "/" -
    // see KNOWN_LIMITATIONS.md, "[B16, owner B17] Login.tsx's 'already
    // logged in' guard drops the user's original destination." pathname +
    // search only (not the full Location object) - Login.tsx only ever
    // needs somewhere to navigate back to, not the rest of history state.
    return (
      <Navigate
        to="/login"
        state={{ from: location.pathname + location.search }}
        replace
      />
    );
  }
  return <>{children}</>;
}
