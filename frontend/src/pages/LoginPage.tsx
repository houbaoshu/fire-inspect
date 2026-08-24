import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { getErrorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const { login, status } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "authenticated") return <Navigate to="/inspections" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      const target = (location.state as LocationState | null)?.from;
      navigate(target?.startsWith("/") && !target.startsWith("//") ? target : "/inspections", { replace: true });
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-brand-panel" aria-label="系统说明">
        <div className="login-brand">
          <span className="login-emblem" aria-hidden="true">火</span>
          <div><strong>消防监督检查</strong><span>AI 辅助系统</span></div>
        </div>
        <div className="login-statement">
          <p className="eyebrow">证据可回溯 · 判断由人完成</p>
          <h1>把检查材料整理成<br />可审核的事实与文书</h1>
          <p>AI 只生成候选内容和初稿。事实确认、执法判断与正式文书始终由检查人员完成。</p>
        </div>
        <div className="login-principles" aria-label="系统原则">
          <span><b>01</b>原始材料完整保留</span>
          <span><b>02</b>每条事实可追溯证据</span>
          <span><b>03</b>失败步骤可独立重试</span>
        </div>
      </section>

      <section className="login-form-panel">
        <form className="login-card" onSubmit={submit}>
          <p className="eyebrow">内网系统</p>
          <h2>登录工作台</h2>
          <p className="form-intro">请使用机构分配的账号。系统不提供默认账号或密码。</p>

          {error ? <div className="notice notice-error" role="alert">{error}</div> : null}

          <label className="field">
            <span>用户名</span>
            <input
              name="username"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              maxLength={150}
              required
              autoFocus
            />
          </label>
          <label className="field">
            <span>密码</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          <button className="button button-primary login-submit" type="submit" disabled={busy || !username.trim() || !password}>
            {busy ? "正在登录…" : "登录"}
          </button>
          <p className="login-help">登录失败多次后系统可能暂时限制尝试。如需重置密码，请联系管理员。</p>
        </form>
        <footer>本系统仅限授权人员使用，操作会记录审计留痕。</footer>
      </section>
    </main>
  );
}
