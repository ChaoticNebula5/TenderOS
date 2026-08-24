import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./useAuth";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { meResponse } = useAuth();

  if (meResponse === undefined) {
    return <p>Loading...</p>;
  }
  if (meResponse === null) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
