import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "@/auth/AuthProvider";
import { getToken } from "@/lib/session";

export function RequireAuth() {
  const { user } = useAuth();
  const location = useLocation();
  if (!user && !getToken()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
