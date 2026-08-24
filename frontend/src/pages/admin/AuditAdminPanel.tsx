import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, getErrorMessage } from "../../api/client";
import { asList, type HistoryEntry, type PaginatedResponse } from "../../api/types";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatDateTime } from "../../utils/format";

interface AuditEntry extends HistoryEntry {
  actor_label?: string;
  request_id?: string;
  event_hash?: string;
}

export function AuditAdminPanel() {
  const [query, setQuery] = useState("");
  const [actionFilter, setActionFilter] = useState("all");
  const auditQuery = useQuery({ queryKey: ["admin-audit"], queryFn: () => api.get<AuditEntry[] | PaginatedResponse<AuditEntry>>("/admin/audit/") });
  const entries = asList(auditQuery.data);
  const actions = useMemo(() => Array.from(new Set(entries.map((entry) => entry.action))), [entries]);
  const filtered = entries.filter((entry) => {
    if (actionFilter !== "all" && entry.action !== actionFilter) return false;
    const keyword = query.trim().toLocaleLowerCase("zh-CN");
    return !keyword || [entry.actor_label, entry.object_type, entry.object_label, entry.action, entry.masked_change_summary].filter(Boolean).some((value) => value!.toLocaleLowerCase("zh-CN").includes(keyword));
  });
  if (auditQuery.isLoading) return <LoadingState label="正在加载审计记录" />;
  if (auditQuery.isError) return <ErrorState message={getErrorMessage(auditQuery.error)} onRetry={() => void auditQuery.refetch()} />;
  return (
    <section aria-labelledby="audit-admin-title">
      <div className="section-heading"><div><h2 id="audit-admin-title">全局审计记录</h2><p>追加写入的最小化永久审计，普通业务删除不会连带删除。</p></div><button className="button button-secondary" type="button" onClick={() => void auditQuery.refetch()}>刷新</button></div>
      <div className="notice notice-info">永久审计不保存完整媒体、完整转写、完整 AI 请求、密钥、会话令牌或完整敏感字段，只显示必要的掩码摘要。</div>
      <div className="panel-toolbar"><label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">搜索审计记录</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索操作人、对象或掩码摘要" /></label><label className="compact-select"><span>动作</span><select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}><option value="all">全部动作</option>{actions.map((action) => <option key={action} value={action}>{action}</option>)}</select></label></div>
      {filtered.length === 0 ? <EmptyState title="没有审计记录" description={query || actionFilter !== "all" ? "请调整筛选条件。" : "重要创建、修改、确认、撤回与删除操作会追加到这里。"} /> : <div className="panel table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>对象</th><th>动作</th><th>掩码变更摘要</th><th>校验</th></tr></thead><tbody>{filtered.map((entry) => <tr key={entry.id}><td>{formatDateTime(entry.occurred_at ?? entry.created_at)}</td><td>{entry.actor_label || "系统"}</td><td><strong>{entry.object_label || entry.object_type}</strong><small>{entry.object_type}</small></td><td>{entry.action}</td><td className="masked-summary">{entry.masked_change_summary || entry.reason || "—"}</td><td><span className={entry.event_hash ? "audit-verified" : "audit-unknown"}>{entry.event_hash ? "摘要链已记录" : "待校验"}</span></td></tr>)}</tbody></table></div>}
    </section>
  );
}
