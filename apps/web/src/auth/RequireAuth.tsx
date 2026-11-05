import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { meResponse, loggedOut } = useAuth();
  const location = useLocation();

  if (meResponse === undefined) {
    return <p>Loading...</p>;
  }
  if (meResponse === null) {
    // Carries the path this redirect interrupted so Login.tsx can send the
    // user back to it once authenticated, instead of always landing on "/" -
    // see KNOWN_LIMITATIONS.md, "[B16, owner B17] Login.tsx's 'already
    // logged in' guard drops the user's original destination." pathname +
    // search + hash (not the full Location object) - Login.tsx only ever
    // needs somewhere to navigate back to, not the rest of history state,
    // but that "somewhere" must include the fragment too: a protected URL
    // opened with one (e.g. a deep link to a specific section) otherwise
    // loses it silently across the login round-trip.
    //
    // Not after a deliberate log out (plan item 39): the next person to sign
    // in may not be this one, and this page may not be theirs.
    return (
      <Navigate
        to="/login"
        state={
          loggedOut ? undefined : { from: location.pathname + location.search + location.hash }
        }
        replace
      />
    );
  }
  return <>{children}</>;
}
