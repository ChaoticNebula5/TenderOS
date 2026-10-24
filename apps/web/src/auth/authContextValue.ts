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
}

// Own file, not AuthContext.tsx: react-refresh/only-export-components
// forbids a component-exporting file from also exporting a plain value.
export const AuthContext = createContext<AuthState | null>(null);
