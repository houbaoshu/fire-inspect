import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { getErrorMessage } from "../api/client";
import { AIAssistant, type AssistantSelection } from "./AIAssistant";

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? "app-nav-link active" : "app-nav-link";
}

function getContext(pathname: string) {
  const match = pathname.match(/^\/inspections\/([^/]+)\/([^/?]+)/);
  if (!match || match[1] === "new") return { inspectionId: undefined, page: pathname };
  return { inspectionId: match[1], page: match[2] };
}

export function AppShell() {
  const { user, logout } = useAuth();
  const location = useLocation();
  useParams();
  const [navOpen, setNavOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantSelection, setAssistantSelection] = useState<AssistantSelection | undefined>();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const context = getContext(location.pathname);

  useEffect(() => {
    const captureSelection = () => {
      const selection = window.getSelection();
      const anchor = selection?.anchorNode;
      const focus = selection?.focusNode;
      const main = document.getElementById("main-content");
      if (!selection || selection.isCollapsed || !anchor || !focus || !main?.contains(anchor) || !main.contains(focus)) {
        setAssistantSelection(undefined);
        return;
      }
      const elementFor = (node: Node) => (node instanceof Element ? node : node.parentElement)
        ?.closest<HTMLElement>("[data-assistant-source-id][data-assistant-revision]");
      const anchorSource = elementFor(anchor);
      const focusSource = elementFor(focus);
      const text = selection.toString().trim().slice(0, 2000);
      const revision = Number(anchorSource?.dataset.assistantRevision);
      const sourceId = anchorSource?.dataset.assistantSourceId;
      if (!text || !sourceId || anchorSource !== focusSource || !Number.isInteger(revision) || revision < 1) {
        setAssistantSelection(undefined);
        return;
      }
      setAssistantSelection({ sourceId, revision, text });
    };
    document.addEventListener("selectionchange", captureSelection);
    return () => document.removeEventListener("selectionchange", captureSelection);
  }, []);

  useEffect(() => {
    setAssistantSelection(undefined);
  }, [location.pathname, location.search]);

  const submitLogout = async () => {
    if (logoutBusy) return;
    setLogoutBusy(true);
    setLogoutError(null);
    try {
      await logout();
    } catch (error) {
      setLogoutError(getErrorMessage(error));
    } finally {
      setLogoutBusy(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="mobile-header">
        <button className="icon-button" type="button" aria-label="打开导航" onClick={() => setNavOpen(true)}>☰</button>
        <Link className="mobile-brand" to="/inspections">消防检查 AI</Link>
        <button className="assistant-mobile-button" type="button" onClick={() => setAssistantOpen((value) => !value)}>AI</button>
      </header>

      {navOpen ? <button className="mobile-backdrop" aria-label="关闭导航" onClick={() => setNavOpen(false)} /> : null}
      <aside className={`app-sidebar ${navOpen ? "open" : ""}`}>
        <Link className="brand" to="/inspections" onClick={() => setNavOpen(false)}>
          <span className="brand-emblem" aria-hidden="true">火</span>
          <span><strong>消防监督检查</strong><small>AI 辅助系统</small></span>
        </Link>

        <nav className="app-nav" aria-label="主导航">
          <span className="nav-group-label">工作</span>
          <NavLink className={navClass} to="/inspections" onClick={() => setNavOpen(false)}>
            <span className="nav-glyph" aria-hidden="true">检</span>检查管理
          </NavLink>
          <NavLink className={navClass} to="/inspections/new" onClick={() => setNavOpen(false)}>
            <span className="nav-glyph" aria-hidden="true">＋</span>新建检查
          </NavLink>

          {user?.role === "admin" ? (
            <>
              <span className="nav-group-label">系统管理</span>
              <NavLink className={navClass} to="/admin/users" onClick={() => setNavOpen(false)}>
                <span className="nav-glyph" aria-hidden="true">人</span>用户管理
              </NavLink>
              <NavLink className={navClass} to="/admin/knowledge" onClick={() => setNavOpen(false)}>
                <span className="nav-glyph" aria-hidden="true">法</span>知识库
              </NavLink>
              <NavLink className={navClass} to="/admin/templates" onClick={() => setNavOpen(false)}>
                <span className="nav-glyph" aria-hidden="true">模</span>模板映射
              </NavLink>
              <NavLink className={navClass} to="/admin/jobs" onClick={() => setNavOpen(false)}>
                <span className="nav-glyph" aria-hidden="true">任</span>任务中心
              </NavLink>
              <NavLink className={navClass} to="/admin/audit" onClick={() => setNavOpen(false)}>
                <span className="nav-glyph" aria-hidden="true">审</span>审计记录
              </NavLink>
            </>
          ) : null}
        </nav>

        <div className="sidebar-user">
          <div className="user-avatar" aria-hidden="true">{user?.username.slice(0, 1) ?? "用"}</div>
          <div className="user-meta">
            <strong>{user?.display_name || user?.username}</strong>
            <span>{user?.role === "admin" ? "管理员" : "检查员"}</span>
          </div>
          <button className="logout-button" type="button" title="退出登录" aria-label="退出登录" disabled={logoutBusy} onClick={() => void submitLogout()}>↪</button>
          {logoutError ? <span className="sidebar-logout-error" role="alert">{logoutError}</span> : null}
        </div>
      </aside>

      <main className="app-main" id="main-content">
        <Outlet />
      </main>

      <div className={`assistant-column ${assistantOpen ? "open" : ""}`}>
        <button className="assistant-close" type="button" onClick={() => setAssistantOpen(false)} aria-label="关闭助手">×</button>
        <AIAssistant inspectionId={context.inspectionId} route={`${location.pathname}${location.search}`} selection={assistantSelection} />
      </div>
    </div>
  );
}
