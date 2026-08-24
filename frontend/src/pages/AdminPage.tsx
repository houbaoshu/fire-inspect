import { NavLink, Navigate, useParams } from "react-router-dom";

import { AuditAdminPanel } from "./admin/AuditAdminPanel";
import { JobsAdminPanel } from "./admin/JobsAdminPanel";
import { KnowledgeAdminPanel } from "./admin/KnowledgeAdminPanel";
import { TemplatesAdminPanel } from "./admin/TemplatesAdminPanel";
import { UsersAdminPanel } from "./admin/UsersAdminPanel";

const SECTIONS = [
  ["users", "用户管理"],
  ["knowledge", "知识文档"],
  ["templates", "模板映射"],
  ["jobs", "任务中心"],
  ["audit", "审计记录"],
] as const;

type AdminSection = (typeof SECTIONS)[number][0];

function isAdminSection(value: string | undefined): value is AdminSection {
  return SECTIONS.some(([section]) => section === value);
}

function panel(section: AdminSection) {
  switch (section) {
    case "users": return <UsersAdminPanel />;
    case "knowledge": return <KnowledgeAdminPanel />;
    case "templates": return <TemplatesAdminPanel />;
    case "jobs": return <JobsAdminPanel />;
    case "audit": return <AuditAdminPanel />;
  }
}

export function AdminPage() {
  const { section } = useParams();
  if (!isAdminSection(section)) return <Navigate to="/admin/users" replace />;
  return (
    <div className="page admin-page">
      <header className="page-header"><div><p className="eyebrow">系统管理</p><h1>管理员控制台</h1><p>管理用户、知识、Word 模板和任务；全局审计仅保存必要的掩码摘要。</p></div></header>
      <nav className="admin-tabs" aria-label="管理员页面">{SECTIONS.map(([path, label]) => <NavLink key={path} to={`/admin/${path}`} className={({ isActive }) => isActive ? "active" : ""}>{label}</NavLink>)}</nav>
      <div className="admin-content">{panel(section)}</div>
    </div>
  );
}
