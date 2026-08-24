import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, createIdempotencyKey, getErrorMessage } from "../api/client";
import { asList, type Inspection, type PaginatedResponse } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { formatDateTime, inspectionTypeLabel } from "../utils/format";

interface DeleteImpact {
  materials?: number;
  facts?: number;
  documents?: number;
  jobs?: number;
  message?: string;
}

function deleteImpactLabel(impact: DeleteImpact): string {
  const parts = [
    impact.materials !== undefined ? `${impact.materials} 份材料` : null,
    impact.facts !== undefined ? `${impact.facts} 条事实` : null,
    impact.documents !== undefined ? `${impact.documents} 份文书` : null,
    impact.jobs !== undefined ? `${impact.jobs} 项任务` : null,
  ].filter(Boolean);
  return impact.message || (parts.length > 0 ? parts.join("、") : "相关业务数据与文件");
}

export function InspectionsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Inspection | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteImpact, setDeleteImpact] = useState<DeleteImpact | null>(null);

  const inspectionsQuery = useQuery({
    queryKey: ["inspections"],
    queryFn: () => api.get<Inspection[] | PaginatedResponse<Inspection>>("/inspections/"),
  });

  const inspections = asList(inspectionsQuery.data);
  const filtered = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase("zh-CN");
    if (!keyword) return inspections;
    return inspections.filter((inspection) =>
      [inspection.unit_name, inspection.unit_address, inspection.responsible_person, inspection.contact_name]
        .filter(Boolean)
        .some((value) => value!.toLocaleLowerCase("zh-CN").includes(keyword)),
    );
  }, [inspections, search]);

  const deleteMutation = useMutation({
    mutationFn: async () => {
      if (!deleteTarget) return;
      await api.delete(`/inspections/${deleteTarget.id}/`, { confirm: true, reason: deleteReason.trim() }, {
        revision: deleteTarget.shared_revision ?? deleteTarget.revision,
        idempotencyKey: createIdempotencyKey("delete-inspection"),
      });
    },
    onSuccess: async () => {
      setDeleteTarget(null);
      setDeleteReason("");
      setDeleteImpact(null);
      await queryClient.invalidateQueries({ queryKey: ["inspections"] });
    },
  });

  const prepareDelete = async (inspection: Inspection) => {
    setDeleteTarget(inspection);
    setDeleteReason("");
    setDeleteImpact(null);
    try {
      setDeleteImpact(await api.get<DeleteImpact>(`/inspections/${inspection.id}/deletion-impact/`));
    } catch {
      setDeleteImpact(null);
    }
  };

  return (
    <div className="page inspections-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">检查管理</p>
          <h1>消防监督检查</h1>
          <p>按一次检查集中管理原始材料、事实证据、法规依据和文书。</p>
        </div>
        <Link className="button button-primary" to="/inspections/new">＋ 新建检查</Link>
      </header>

      <section className="summary-strip" aria-label="检查概况">
        <div><span>可访问检查</span><strong>{inspections.length}</strong></div>
        <div><span>处理中任务</span><strong>{inspections.reduce((sum, item) => sum + (item.processing_summary?.running ?? 0), 0)}</strong></div>
        <div><span>待处理失败</span><strong>{inspections.reduce((sum, item) => sum + (item.processing_summary?.failed ?? 0), 0)}</strong></div>
      </section>

      <section className="panel">
        <div className="panel-toolbar">
          <label className="search-field">
            <span className="sr-only">搜索检查</span>
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索单位、地址、负责人或联系人"
            />
          </label>
          <span className="result-count">共 {filtered.length} 项</span>
        </div>

        {inspectionsQuery.isLoading ? <LoadingState label="正在加载检查列表" /> : null}
        {inspectionsQuery.isError ? (
          <ErrorState message={getErrorMessage(inspectionsQuery.error)} onRetry={() => void inspectionsQuery.refetch()} />
        ) : null}
        {!inspectionsQuery.isLoading && !inspectionsQuery.isError && filtered.length === 0 ? (
          <EmptyState
            title={search ? "没有匹配的检查" : "还没有检查记录"}
            description={search ? "请尝试单位名称或地址中的其他关键词。" : "创建检查后即可上传材料并开始处理。"}
            action={!search ? <Link className="button button-primary" to="/inspections/new">创建第一项检查</Link> : undefined}
          />
        ) : null}

        {filtered.length > 0 ? (
          <div className="inspection-list">
            {filtered.map((inspection) => {
              const summary = inspection.processing_summary;
              return (
                <article className="inspection-row" key={inspection.id}>
                  <Link className="inspection-row-main" to={`/inspections/${inspection.id}/materials`}>
                    <div className="inspection-date">
                      <strong>{new Date(inspection.inspection_at).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit" })}</strong>
                      <span>{new Date(inspection.inspection_at).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric" })}</span>
                    </div>
                    <div className="inspection-copy">
                      <div className="inspection-title-line">
                        <h2>{inspection.unit_name}</h2>
                        {summary?.failed ? <StatusPill status="failed" label={`${summary.failed} 项失败`} /> : null}
                        {summary?.running ? <StatusPill status="running" label={`${summary.running} 项处理中`} /> : null}
                      </div>
                      <p>{inspection.unit_address || "未填写地址"}</p>
                      <div className="inspection-meta">
                        <span>{inspectionTypeLabel(inspection.inspection_type)}</span>
                        <span>检查时间 {formatDateTime(inspection.inspection_at)}</span>
                        <span>检查人员 {inspection.owner?.display_name || inspection.owner?.username || "—"}</span>
                      </div>
                    </div>
                    <span className="row-arrow" aria-hidden="true">→</span>
                  </Link>
                  <button
                    className="row-menu-button"
                    type="button"
                    aria-label={`删除 ${inspection.unit_name}`}
                    title="删除检查"
                    onClick={() => void prepareDelete(inspection)}
                  >
                    ×
                  </button>
                </article>
              );
            })}
          </div>
        ) : null}
      </section>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除整项检查？"
        tone="danger"
        confirmLabel="确认删除"
        requireReason
        reason={deleteReason}
        onReasonChange={setDeleteReason}
        busy={deleteMutation.isPending}
        onCancel={() => {
          if (!deleteMutation.isPending) setDeleteTarget(null);
        }}
        onConfirm={() => deleteMutation.mutate()}
        description={
          <>
            <p>将删除“{deleteTarget?.unit_name}”的材料、转写、事实、证据、文书及派生文件。此操作不可撤销。</p>
            {deleteImpact ? (
              <p className="impact-summary">影响：{deleteImpactLabel(deleteImpact)}。</p>
            ) : null}
            <p>系统会永久保留最小化删除审计记录。</p>
            {deleteMutation.isError ? <span className="form-error">{getErrorMessage(deleteMutation.error)}</span> : null}
          </>
        }
      />
    </div>
  );
}
