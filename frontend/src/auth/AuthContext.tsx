import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api, ApiError, getCookie } from "../api/client";
import type { User } from "../api/types";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export interface AuthContextValue {
  user: User | null;
  status: AuthStatus;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

function unwrapUser(response: User | { user: User }): User {
  return "user" in response ? response.user : response;
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");

  const refresh = useCallback(async () => {
    try {
      const response = await api.get<User | { user: User }>("/auth/me/");
      setUser(unwrapUser(response));
      setStatus("authenticated");
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setUser(null);
        setStatus("anonymous");
        return;
      }
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null);
      setStatus("anonymous");
    };
    window.addEventListener("fire-inspect:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("fire-inspect:unauthorized", handleUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    if (!getCookie("csrftoken")) await api.get("/auth/csrf/");
    const response = await api.post<User | { user: User }>("/auth/login/", { username, password });
    setUser(unwrapUser(response));
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    await api.post("/auth/logout/", {});
    setUser(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, status, login, logout, refresh }),
    [login, logout, refresh, status, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
