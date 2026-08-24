import type { PropsWithChildren } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { LoadingState } from "../components/States";
import { useAuth } from "./AuthContext";

export function RequireAuth({ children }: PropsWithChildren) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <LoadingState label="正在验证登录状态" fullPage />;
  if (status === "anonymous") {
    return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  }
  return children;
}
