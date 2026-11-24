import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { loginRequest } from "@/lib/api";
import { clearSession, loadSession, onUnauthorized, setSession, type AuthUser } from "@/lib/session";

type AuthContextValue = {
  user: AuthUser | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  // Cached queries belong to the signed-in company. Drop them whenever the
  // session changes so the next user never sees the previous company's data.
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(() => loadSession().user);

  useEffect(() => {
    return onUnauthorized(() => {
      queryClient.clear();
      setUser(null);
      navigate("/login", { replace: true });
    });
  }, [navigate, queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await loginRequest(email, password);
      queryClient.clear();
      setSession(result.access_token, result.user);
      setUser(result.user);
      navigate("/", { replace: true });
    },
    [navigate, queryClient],
  );

  const logout = useCallback(() => {
    clearSession();
    queryClient.clear();
    setUser(null);
    navigate("/login", { replace: true });
  }, [navigate, queryClient]);

  const value = useMemo(() => ({ user, login, logout }), [user, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return ctx;
}
