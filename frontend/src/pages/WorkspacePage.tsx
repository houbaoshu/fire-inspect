import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, Navigate, useParams } from "react-router-dom";

import { api, getErrorMessage } from "../api/client";
import type { Inspection } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { ErrorState, LoadingState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import {
  formatDateTime,
  INSPECTION_TYPES,
  inspectionTypeLabel,
  shanghaiDateTimeInputToIso,
  toShanghaiDateTimeInput,
} from "../utils/format";
import { DocumentPanel } from "./workspace/DocumentPanel";
import { FactsPanel } from "./workspace/FactsPanel";
import { HistoryPanel } from "./workspace/HistoryPanel";
import { MaterialsPanel } from "./workspace/MaterialsPanel";
import { RegulationsPanel } from "./workspace/RegulationsPanel";
import { TranscriptsPanel } from "./workspace/TranscriptsPanel";

const SECTIONS = [
  ["materials", "材料", "材"],
  ["transcripts", "转写", "转"],
  ["facts", "事实与证据", "证"],
  ["regulations", "法规", "法"],
  ["record", "监督检查记录", "记"],
  ["photo-document", "照片文书", "照"],
  ["interview", "询问笔录", "问"],
  ["history", "历史", "史"],
] as const;

type Section = (typeof SECTIONS)[number][0];

function isSection(value: string | undefined): value is Section {
  return SECTIONS.some(([section]) => section === value);
}

interface EditFields {
  unit_name: string;
  unit_address: string;
  responsible_person: string;
  contact_name: string;
  contact_phone: string;
  inspection_at: string;
  inspection_type: string;
  general_notes: string;
}

function toLocalDateTime(value: string) {
  return toShanghaiDateTimeInput(value);
}

function buildEditFields(inspection: Inspection): EditFields {
  return {
    unit_name: inspection.unit_name,
    unit_address: inspection.unit_address,
    responsible_person: inspection.responsible_person ?? "",
    contact_name: inspection.contact_name ?? "",
    contact_phone: inspection.contact_phone ?? "",
    inspection_at: toLocalDateTime(inspection.inspection_at),
    inspection_type: inspection.inspection_type,
    general_notes: inspection.general_notes ?? "",
  };
}

function sectionPanel(section: Section, inspectionId: string) {
  switch (section) {
    case "materials":
      return <MaterialsPanel inspectionId={inspectionId} />;
    case "transcripts":
      return <TranscriptsPanel inspectionId={inspectionId} />;
    case "facts":
      return <FactsPanel inspectionId={inspectionId} />;
    case "regulations":
      return <RegulationsPanel inspectionId={inspectionId} />;
    case "record":
      return <DocumentPanel inspectionId={inspectionId} type="record" />;
    case "photo-document":
      return <DocumentPanel inspectionId={inspectionId} type="photo" />;
    case "interview":
      return <DocumentPanel inspectionId={inspectionId} type="interview" />;
    case "history":
      return <HistoryPanel inspectionId={inspectionId} />;
  }
}

export function WorkspacePage() {
  const { inspectionId, section } = useParams();
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [editFields, setEditFields] = useState<EditFields | null>(null);
  const [confirmSharedChange, setConfirmSharedChange] = useState(false);

  const inspectionQuery = useQuery({
    queryKey: ["inspection", inspectionId],
    queryFn: () => api.get<Inspection>(`/inspections/${inspectionId}/`),
    enabled: Boolean(inspectionId),
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!inspectionId || !editFields || !inspectionQuery.data) throw new Error("missing inspection");
      return api.patch<Inspection>(`/inspections/${inspectionId}/`, {
        ...editFields,
        unit_name: editFields.unit_name.trim(),
        unit_address: editFields.unit_address.trim(),
        responsible_person: editFields.responsible_person.trim(),
        contact_name: editFields.contact_name.trim(),
        contact_phone: editFields.contact_phone.trim(),
        general_notes: editFields.general_notes.trim(),
        inspection_at: shanghaiDateTimeInputToIso(editFields.inspection_at),
        shared_revision: inspectionQuery.data.shared_revision ?? inspectionQuery.data.revision,
      }, { revision: inspectionQuery.data.shared_revision ?? inspectionQuery.data.revision });
    },
    onSuccess: async () => {
      setConfirmSharedChange(false);
      setEditOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["inspection", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  useEffect(() => {
    if (inspectionQuery.data && editOpen && !editFields) setEditFields(buildEditFields(inspectionQuery.data));
  }, [editFields, editOpen, inspectionQuery.data]);

  if (!inspectionId) return <Navigate to="/inspections" replace />;
  if (!isSection(section)) return <Navigate to={`/inspections/${inspectionId}/materials`} replace />;
  if (inspectionQuery.isLoading) return <LoadingState label="正在打开检查工作台" />;
  if (inspectionQuery.isError || !inspectionQuery.data) {
    return <div className="page"><ErrorState message={getErrorMessage(inspectionQuery.error)} onRetry={() => void inspectionQuery.refetch()} /></div>;
  }

  const inspection = inspectionQuery.data;
  const summary = inspection.processing_summary;
  const originalEditFields = buildEditFields(inspection);
  const hasSharedChanges = Boolean(editFields && (Object.keys(originalEditFields) as Array<keyof EditFields>).some((key) => editFields[key] !== originalEditFields[key]));

  return (
    <div className="workspace-page">
      <header className="workspace-header">
        <div className="workspace-breadcrumb"><Link to="/inspections">检查管理</Link><span>/</span><span>检查工作台</span></div>
        <div className="workspace-title-row">
          <div data-assistant-source-id={inspection.id} data-assistant-revision={inspection.shared_revision ?? inspection.revision}>
            <h1>{inspection.unit_name}</h1>
            <p>{inspection.unit_address || "未填写单位地址"}</p>
          </div>
          <button
            className="button button-secondary"
            type="button"
            onClick={() => {
              setEditFields(buildEditFields(inspection));
              setEditOpen(true);
            }}
          >编辑共享信息</button>
        </div>
        <div className="workspace-meta">
          <span><b>检查时间</b>{formatDateTime(inspection.inspection_at)}</span>
          <span><b>检查类型</b>{inspectionTypeLabel(inspection.inspection_type)}</span>
          <span><b>负责人</b>{inspection.responsible_person || "待补充"}</span>
          {summary?.running ? <StatusPill status="running" label={`${summary.running} 项任务处理中`} /> : null}
          {summary?.queued ? <StatusPill status="queued" label={`${summary.queued} 项任务排队中`} /> : null}
          {summary?.failed ? <StatusPill status="failed" label={`${summary.failed} 项任务失败`} /> : null}
        </div>
      </header>

      <nav className="workspace-tabs" aria-label="检查工作台">
        {SECTIONS.map(([path, label, glyph]) => (
          <NavLink
            key={path}
            to={`/inspections/${inspectionId}/${path}`}
            className={({ isActive }) => (isActive ? "workspace-tab active" : "workspace-tab")}
          >
            <span aria-hidden="true">{glyph}</span>{label}
          </NavLink>
        ))}
      </nav>

      <div className="workspace-content">{sectionPanel(section, inspectionId)}</div>

      {editOpen && editFields ? (
        <div className="drawer-backdrop" role="presentation" onMouseDown={() => setEditOpen(false)}>
          <section className="edit-drawer" role="dialog" aria-modal="true" aria-labelledby="edit-shared-title" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><p className="eyebrow">检查共享字段</p><h2 id="edit-shared-title">编辑检查信息</h2></div><button className="icon-button" type="button" aria-label="关闭" onClick={() => setEditOpen(false)}>×</button></header>
            <div className="notice notice-warning">修改任一有效共享字段，会使本检查三类已确认文书同步退回初稿，并写入原因和历史。</div>
            <div className="drawer-form">
              <label className="field"><span>单位名称</span><input value={editFields.unit_name} onChange={(event) => setEditFields({ ...editFields, unit_name: event.target.value })} /></label>
              <label className="field"><span>地址</span><input value={editFields.unit_address} onChange={(event) => setEditFields({ ...editFields, unit_address: event.target.value })} /></label>
              <div className="form-grid two-columns">
                <label className="field"><span>负责人</span><input value={editFields.responsible_person} onChange={(event) => setEditFields({ ...editFields, responsible_person: event.target.value })} /></label>
                <label className="field"><span>联系人</span><input value={editFields.contact_name} onChange={(event) => setEditFields({ ...editFields, contact_name: event.target.value })} /></label>
                <label className="field"><span>联系电话</span><input value={editFields.contact_phone} onChange={(event) => setEditFields({ ...editFields, contact_phone: event.target.value })} /></label>
                <label className="field"><span>检查时间</span><input type="datetime-local" value={editFields.inspection_at} onChange={(event) => setEditFields({ ...editFields, inspection_at: event.target.value })} /></label>
              </div>
              <label className="field"><span>检查类型</span><select value={editFields.inspection_type} onChange={(event) => setEditFields({ ...editFields, inspection_type: event.target.value })}>{INSPECTION_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <label className="field"><span>通用备注</span><textarea rows={4} value={editFields.general_notes} onChange={(event) => setEditFields({ ...editFields, general_notes: event.target.value })} /></label>
            </div>
            {updateMutation.isError ? <div className="notice notice-error">{getErrorMessage(updateMutation.error)}</div> : null}
            <footer><button className="button button-ghost" type="button" onClick={() => setEditOpen(false)}>取消</button><button className="button button-primary" type="button" disabled={!hasSharedChanges || !editFields.unit_name.trim() || !editFields.unit_address.trim() || !editFields.inspection_at} onClick={() => setConfirmSharedChange(true)}>保存共享信息</button></footer>
          </section>
        </div>
      ) : null}

      <ConfirmDialog
        open={confirmSharedChange}
        title="确认修改共享字段？"
        tone="warning"
        confirmLabel="确认修改并使文书退回初稿"
        busy={updateMutation.isPending}
        onCancel={() => setConfirmSharedChange(false)}
        onConfirm={() => updateMutation.mutate()}
        description={<p>保存后，三类已确认文书都将立即退回初稿。文书不会在后台自动重新生成。</p>}
      />
    </div>
  );
}
