import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import { jobLabel } from "../../api/jobContract";
import { asList, type JobStatus, type JobSummary, type PaginatedResponse } from "../../api/types";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { formatDateTime } from "../../utils/format";

export function JobsAdminPanel() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<"all" | JobStatus>("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const jobsQuery = useQuery({
    queryKey: ["admin-jobs"],
    queryFn: () => api.get<JobSummary[] | PaginatedResponse<JobSummary>>("/admin/jobs/"),
    refetchInterval: (query) => asList(query.state.data as JobSummary[] | PaginatedResponse<JobSummary> | undefined).some((job) => job.status === "queued" || job.status === "running") ? 3000 : 10_000,
  });
  const jobs = asList(jobsQuery.data);
  const types = useMemo(() => Array.from(new Set(jobs.map((job) => job.task_type))), [jobs]);
  const filtered = jobs.filter((job) => (statusFilter === "all" || job.status === statusFilter) && (typeFilter === "all" || job.task_type === typeFilter));
  const retryMutation = useMutation({
    mutationFn: (jobId: string) => api.post(`/jobs/${jobId}/retry/`, {}, { idempotencyKey: createIdempotencyKey(`retry-${jobId}`) }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["admin-jobs"] }),
  });

  if (jobsQuery.isLoading) return <LoadingState label="正在加载任务" />;
  if (jobsQuery.isError) return <ErrorState message={getErrorMessage(jobsQuery.error)} onRetry={() => void jobsQuery.refetch()} />;
  const counts = jobs.reduce<Record<string, number>>((result, job) => ({ ...result, [job.status]: (result[job.status] ?? 0) + 1 }), {});
  return (
    <section aria-labelledby="jobs-admin-title">
      <div className="section-heading"><div><h2 id="jobs-admin-title">任务中心</h2><p>耗时步骤由后台任务运行。页面短轮询可见状态，不需要 WebSocket。</p></div><button className="button button-secondary" type="button" onClick={() => void jobsQuery.refetch()}>刷新</button></div>
      <div className="summary-strip compact"><div><span>排队中</span><strong>{counts.queued ?? 0}</strong></div><div><span>处理中</span><strong>{counts.running ?? 0}</strong></div><div><span>失败</span><strong>{counts.failed ?? 0}</strong></div><div><span>已完成</span><strong>{counts.succeeded ?? 0}</strong></div></div>
      <div className="panel-toolbar admin-filter-toolbar"><div className="filter-bar" role="group" aria-label="筛选任务状态">{([['all', '全部'], ['queued', '排队中'], ['running', '处理中'], ['failed', '失败'], ['succeeded', '已完成'], ['superseded', '已过期']] as const).map(([value, label]) => <button className={statusFilter === value ? "active" : ""} type="button" key={value} onClick={() => setStatusFilter(value)}>{label}<span>{value === "all" ? jobs.length : counts[value] ?? 0}</span></button>)}</div><label className="compact-select"><span>任务类型</span><select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="all">全部类型</option>{types.map((type) => <option value={type} key={type}>{jobLabel(type)}</option>)}</select></label></div>
      {filtered.length === 0 ? <EmptyState title="没有符合条件的任务" description="新建处理或生成任务后会显示在这里。重复提交会复用幂等任务。" /> : <div className="panel table-wrap"><table><thead><tr><th>任务</th><th>范围</th><th>状态</th><th>尝试</th><th>时间</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{filtered.map((job) => <tr key={job.id}><td><strong>{jobLabel(job.task_type)}</strong><small>{job.id.slice(0, 12)}</small></td><td>{job.scope_label || (job.inspection_id ? `检查 ${job.inspection_id.slice(0, 8)}` : "全局")}</td><td><StatusPill status={job.status} />{job.safe_error_message ? <small className="form-error job-error">{job.safe_error_message}</small> : null}</td><td>{job.attempt_count ?? 0} / {job.max_attempts ?? "—"}</td><td><span>{formatDateTime(job.started_at ?? job.created_at)}</span>{job.finished_at ? <small>结束 {formatDateTime(job.finished_at)}</small> : null}</td><td>{job.status === "failed" ? <button className="button button-secondary button-small" type="button" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate(job.id)}>仅重试失败步骤</button> : null}</td></tr>)}</tbody></table></div>}
      {retryMutation.isError ? <ErrorState message={getErrorMessage(retryMutation.error)} /> : null}
    </section>
  );
}
