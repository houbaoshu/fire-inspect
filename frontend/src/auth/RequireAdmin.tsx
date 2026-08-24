import type { PropsWithChildren } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "./AuthContext";

export function RequireAdmin({ children }: PropsWithChildren) {
  const { user } = useAuth();
  if (user?.role === "admin") return children;

  return (
    <main className="standalone-state" aria-labelledby="forbidden-title">
      <div className="state-mark" aria-hidden="true">403</div>
      <h1 id="forbidden-title">无权访问</h1>
      <p>此页面仅向管理员开放。接口仍会独立校验权限。</p>
      <Link className="button button-primary" to="/inspections">返回检查列表</Link>
    </main>
  );
}
