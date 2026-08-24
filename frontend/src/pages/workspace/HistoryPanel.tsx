import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, getErrorMessage } from "../../api/client";
import {
  asList,
  historyActor,
  historyNewValue,
  historyOldValue,
  type HistoryEntry,
  type PaginatedResponse,
} from "../../api/types";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { compactJson, formatDateTime } from "../../utils/format";
import type { WorkspacePanelProps } from "./workspace";

function objectTypeLabel(value: string) {
  return ({
    inspection: "检查",
    material: "材料",
    transcript: "转写",
    transcript_segment: "转写片段",
    fact: "事实",
    evidence: "证据",
    document: "文书",
    document_field: "文书字段",
    template_mapping: "模板映射",
  } as Record<string, string>)[value] ?? value;
}

export function HistoryPanel({ inspectionId }: WorkspacePanelProps) {
  const [filter, setFilter] = useState("all");
  const historyQuery = useQuery({
    queryKey: ["inspection-history", inspectionId],
    queryFn: () => api.get<HistoryEntry[] | PaginatedResponse<HistoryEntry>>(`/inspections/${inspectionId}/history/`),
  });
  const entries = asList(historyQuery.data);
  const types = useMemo(() => Array.from(new Set(entries.map((entry) => entry.object_type))), [entries]);
  const filtered = filter === "all" ? entries : entries.filter((entry) => entry.object_type === filter);

  if (historyQuery.isLoading) return <LoadingState label="正在加载检查历史" />;
  if (historyQuery.isError) return <ErrorState message={getErrorMessage(historyQuery.error)} onRetry={() => void historyQuery.refetch()} />;

  return (
    <section aria-labelledby="history-title">
      <div className="section-heading"><div><p className="eyebrow">当前检查详细留痕</p><h2 id="history-title">修改历史</h2><p>显示操作人、时间、对象、动作以及旧值到新值；内容仅对有权访问本检查的用户可见。</p></div></div>
      <div className="notice notice-info">这里是活动业务对象的详细历史。检查删除后，详细业务内容会删除；系统另行保留不可篡改的最小化永久审计记录。</div>
      {entries.length > 0 ? <div className="filter-bar" role="group" aria-label="筛选历史对象"><button className={filter === "all" ? "active" : ""} type="button" onClick={() => setFilter("all")}>全部<span>{entries.length}</span></button>{types.map((type) => <button className={filter === type ? "active" : ""} type="button" key={type} onClick={() => setFilter(type)}>{objectTypeLabel(type)}<span>{entries.filter((entry) => entry.object_type === type).length}</span></button>)}</div> : null}
      {filtered.length === 0 ? <EmptyState title="暂无修改历史" description="事实确认、字段编辑、文书确认与退回等重要操作会显示在这里。" /> : <div className="history-timeline">{filtered.map((entry) => { const oldValue = historyOldValue(entry); const newValue = historyNewValue(entry); return <article className="history-entry" key={entry.id}><div className="timeline-dot" aria-hidden="true" /><div className="history-entry-card panel"><header><div><span className="object-tag">{objectTypeLabel(entry.object_type)}</span><h3>{entry.object_label || entry.action}</h3></div><time>{formatDateTime(entry.created_at ?? entry.occurred_at)}</time></header><div className="history-actor"><strong>{historyActor(entry)}</strong><span>{entry.action}</span></div>{entry.reason ? <p className="history-reason">原因：{entry.reason}</p> : null}{oldValue !== undefined || newValue !== undefined ? <details><summary>查看旧值与新值</summary><div className="history-diff"><div><span>旧值</span><pre>{compactJson(oldValue)}</pre></div><span aria-hidden="true">→</span><div><span>新值</span><pre>{compactJson(newValue)}</pre></div></div></details> : null}</div></article>; })}</div>}
    </section>
  );
}
