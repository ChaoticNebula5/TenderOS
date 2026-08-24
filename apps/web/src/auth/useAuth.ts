import { useContext } from "react";
import { AuthContext } from "./authContextValue";
import type { AuthState } from "./authContextValue";

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
