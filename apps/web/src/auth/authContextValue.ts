import { createContext } from "react";
import type { MeResponse } from "../api/client";

export interface AuthState {
  // undefined: still loading /auth/me. null: no valid session.
  meResponse: MeResponse | null | undefined;
  // The first membership's organization_id - this project's 17 screens
  // have no org-switcher screen, so a user with more than one membership
  // is out of scope for B13 (function before polish); revisit if that
  // ever becomes a real scenario.
  organizationId: string | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  // True when the session ended because the user chose to log out, until the
  // next successful sign-in. RequireAuth reads it: an expired session returns
  // to its page after logging in, a deliberate log out starts afresh.
  loggedOut: boolean;
}

// Own file, not AuthContext.tsx: react-refresh/only-export-components
// forbids a component-exporting file from also exporting a plain value.
export const AuthContext = createContext<AuthState | null>(null);
