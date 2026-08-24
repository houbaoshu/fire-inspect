import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import {
  asList,
  type DocumentField,
  type DocumentType,
  type Fact,
  type Inspection,
  type InspectionDocument,
  type PaginatedResponse,
} from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { formatDateTime, inspectionTypeLabel } from "../../utils/format";
import type { WorkspacePanelProps } from "./workspace";

const DOCUMENT_LABELS: Record<DocumentType, string> = {
  record: "监督检查记录",
  photo: "照片文书",
  interview: "询问笔录",
};

export const DOCUMENT_GENERATION_POLL_INTERVAL_MS = 3000;

export function documentGenerationPollInterval(
  status: InspectionDocument["generator_status"],
): number | false {
  return status === "queued" || status === "running"
    ? DOCUMENT_GENERATION_POLL_INTERVAL_MS
    : false;
}

const FIELD_LABELS: Record<string, string> = {
  record_number: "编号",
  inspection_content: "检查内容和情况",
  photo_location: "拍摄地点",
  shooting_location: "拍摄地点",
  photographer: "摄制人",
  photo_taken_at: "拍摄时间",
  shooting_time: "拍摄时间",
  confirmer: "确认人",
  interview_number: "询问次数",
  interview_start_at: "询问开始时间",
  started_at: "询问开始时间",
  interview_end_at: "询问结束时间",
  ended_at: "询问结束时间",
  interview_location: "询问地点",
  location: "询问地点",
  questioner: "询问人",
  interviewer_name: "询问人",
  questioner_work_unit: "询问人工作单位",
  interviewer_unit: "询问人工作单位",
  recorder: "记录人",
  recorder_name: "记录人",
  recorder_work_unit: "记录人工作单位",
  recorder_unit: "记录人工作单位",
  interviewee_name: "被询问人姓名",
  interviewee_gender: "性别",
  gender: "性别",
  age: "年龄",
  interviewee_birth_date: "出生日期",
  birth_date: "出生日期",
  interviewee_id_type: "证件种类",
  id_type: "证件种类",
  interviewee_id_number: "证件号码",
  id_number: "证件号码",
  interviewee_address: "地址",
  registered_address: "户籍所在地",
  current_address: "现住址",
  interviewee_phone: "联系方式",
  contact: "联系方式",
};

function normalizeFields(fields: InspectionDocument["fields"]): DocumentField[] {
  if (Array.isArray(fields)) return fields;
  if (!fields) return [];
  return Object.entries(fields).map(([key, raw]) => {
    if (raw && typeof raw === "object" && "value" in raw) {
      const value = raw as { value: unknown; display_name?: string; origin?: DocumentField["origin"]; manual_locked?: boolean; shared?: boolean };
      return { key, display_name: value.display_name || FIELD_LABELS[key] || "未命名业务字段", value: value.value, origin: value.origin, manual_locked: value.manual_locked, shared: value.shared };
    }
    return { key, display_name: FIELD_LABELS[key] || "未命名业务字段", value: raw };
  });
}

function safeDownloadUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.origin !== window.location.origin) return null;
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

function FieldEditor({ document, field, inspectionId }: { document: InspectionDocument; field: DocumentField; inspectionId: string }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(field.value == null ? "" : String(field.value));
  const multiline = ["inspection_content", "general_notes"].includes(field.key) || value.length > 100;

  useEffect(() => setValue(field.value == null ? "" : String(field.value)), [field.value]);

  const mutation = useMutation({
    mutationFn: () => api.patch(`/documents/${document.id}/fields/${encodeURIComponent(field.key)}/`, {
      value,
      origin: "manual",
      manual_locked: true,
    }, { revision: document.revision }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });

  return (
    <label className={`document-field ${multiline ? "wide" : ""}`}>
      <span className="document-field-label"><b>{field.display_name || FIELD_LABELS[field.key] || field.key}</b><small className={field.origin === "manual" || field.manual_locked ? "origin-manual" : "origin-ai"}>{field.origin === "manual" || field.manual_locked ? "人工内容 · 已保护" : field.origin === "ai" ? "AI 初稿" : "系统字段"}</small></span>
      {multiline ? <textarea rows={5} value={value} onChange={(event) => setValue(event.target.value)} maxLength={20_000} /> : <input value={value} onChange={(event) => setValue(event.target.value)} maxLength={2000} />}
      <span className="field-save-row">{mutation.isError ? <small className="form-error">{getErrorMessage(mutation.error)}</small> : <span />}
        <button className="button button-secondary button-small" type="button" disabled={String(field.value ?? "") === value || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "保存中" : "保存"}</button>
      </span>
    </label>
  );
}

function FactDependenciesEditor({
  document,
  inspectionId,
}: {
  document: InspectionDocument;
  inspectionId: string;
}) {
  const queryClient = useQueryClient();
  const factsQuery = useQuery({
    queryKey: ["facts", inspectionId],
    queryFn: () => api.get<Fact[] | PaginatedResponse<Fact>>(`/inspections/${inspectionId}/facts/`),
  });
  const confirmedFacts = useMemo(
    () => asList(factsQuery.data).filter((fact) => fact.review_state === "confirmed"),
    [factsQuery.data],
  );
  const [selectedIds, setSelectedIds] = useState<string[]>(document.fact_dependency_ids ?? []);
  useEffect(() => {
    setSelectedIds(document.fact_dependency_ids ?? []);
  }, [document.fact_dependency_ids]);
  const mutation = useMutation({
    mutationFn: () => api.put(
      `/documents/${document.id}/fact-dependencies/`,
      { fact_ids: selectedIds },
      { revision: document.revision },
    ),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  const unchanged = [...selectedIds].sort().join("|") === [...(document.fact_dependency_ids ?? [])].sort().join("|");

  return (
    <section className="panel document-fact-dependencies">
      <div className="subsection-heading">
        <div>
          <h3>引用的已确认事实</h3>
          <p>生成只使用这里勾选的已确认事实；事实正文、状态或证据变化时，仅使实际引用它的文书退回初稿。</p>
        </div>
      </div>
      {factsQuery.isLoading ? <LoadingState /> : confirmedFacts.length === 0 ? (
        <EmptyState title="暂无已确认事实" description="先在事实页完成确认；之后可以在这里选择文书实际引用的事实。" />
      ) : (
        <div className="fact-dependency-list">
          {confirmedFacts.map((fact) => (
            <label key={fact.id} className="fact-dependency-option">
              <input
                type="checkbox"
                checked={selectedIds.includes(fact.id)}
                onChange={(event) => setSelectedIds((current) => event.target.checked
                  ? [...current, fact.id]
                  : current.filter((id) => id !== fact.id))}
              />
              <span>{fact.content}</span>
              {fact.needs_verification ? <small>待核实</small> : null}
            </label>
          ))}
        </div>
      )}
      {factsQuery.isError ? <ErrorState message={getErrorMessage(factsQuery.error)} onRetry={() => void factsQuery.refetch()} /> : null}
      {mutation.isError ? <div className="notice notice-error">{getErrorMessage(mutation.error)}</div> : null}
      <div className="form-actions compact">
        <button className="button button-secondary button-small" type="button" disabled={factsQuery.isLoading || unchanged || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? "保存中" : "保存事实依赖"}
        </button>
      </div>
    </section>
  );
}

function PhotoItemsEditor({ document, inspectionId }: { document: InspectionDocument; inspectionId: string }) {
  const queryClient = useQueryClient();
  const [candidateOpen, setCandidateOpen] = useState(false);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const candidatesQuery = useQuery({
    queryKey: ["photo-candidates", document.id],
    queryFn: () => api.get<Array<{ id: string; label: string; thumbnail_url?: string }>>(`/documents/${document.id}/photo-candidates/`),
    enabled: candidateOpen,
  });
  const addMutation = useMutation({
    mutationFn: () => api.post(`/documents/${document.id}/photo-items/`, { source_ids: selectedCandidateIds }, { revision: document.revision, idempotencyKey: createIdempotencyKey("add-document-photos") }),
    onSuccess: async () => {
      setSelectedCandidateIds([]);
      setCandidateOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["document", document.id] });
    },
  });

  return (
    <div className="document-repeating-section">
      <div className="subsection-heading"><div><h3>逐照片内容</h3><p>每张照片生成一个完整纵向证据区块；共享拍摄字段在上方一次维护。</p></div><button className="button button-secondary button-small" type="button" onClick={() => setCandidateOpen((value) => !value)}>＋ 选择照片</button></div>
      {candidateOpen ? (
        <div className="candidate-picker">
          {candidatesQuery.isLoading ? <LoadingState /> : (candidatesQuery.data ?? []).length === 0 ? <p className="muted">暂无可选关键画面或照片，可先在材料页上传补充照片。</p> : <div className="candidate-grid">{candidatesQuery.data!.map((candidate) => {
            const thumbnailUrl = safeDownloadUrl(candidate.thumbnail_url);
            return <label key={candidate.id} className={selectedCandidateIds.includes(candidate.id) ? "candidate-card selected" : "candidate-card"}>{thumbnailUrl ? <img src={thumbnailUrl} alt="" /> : <span className="candidate-placeholder">照片</span>}<input type="checkbox" checked={selectedCandidateIds.includes(candidate.id)} onChange={(event) => setSelectedCandidateIds((current) => event.target.checked ? [...current, candidate.id] : current.filter((id) => id !== candidate.id))} /><strong>{candidate.label}</strong></label>;
          })}</div>}
          {candidatesQuery.isError ? <ErrorState message={getErrorMessage(candidatesQuery.error)} onRetry={() => void candidatesQuery.refetch()} /> : null}
          {addMutation.isError ? <div className="notice notice-error">{getErrorMessage(addMutation.error)}</div> : null}
          <div className="form-actions compact"><button className="button button-primary button-small" type="button" disabled={selectedCandidateIds.length === 0 || addMutation.isPending} onClick={() => addMutation.mutate()}>添加 {selectedCandidateIds.length || ""} 张照片</button></div>
        </div>
      ) : null}
      {(document.photo_items ?? []).length === 0 ? <EmptyState title="尚未选择照片" description="选择清晰关键帧或上传照片后，可逐张编辑反映内容和说明。" /> : <div className="photo-item-list">{document.photo_items!.map((item) => <PhotoItem key={item.id} item={item} document={document} inspectionId={inspectionId} />)}</div>}
    </div>
  );
}

function PhotoItem({ item, document, inspectionId }: { item: NonNullable<InspectionDocument["photo_items"]>[number]; document: InspectionDocument; inspectionId: string }) {
  const queryClient = useQueryClient();
  const [reflectedContent, setReflectedContent] = useState(item.reflected_content ?? "");
  const [description, setDescription] = useState(item.description ?? "");
  useEffect(() => {
    setReflectedContent(item.reflected_content ?? "");
    setDescription(item.description ?? "");
  }, [item.description, item.reflected_content]);
  const mutation = useMutation({
    mutationFn: () => api.patch(`/documents/${document.id}/photo-items/${item.id}/`, { reflected_content: reflectedContent, description, origin: "manual" }, { revision: document.revision }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  const removeMutation = useMutation({
    mutationFn: () => api.delete(`/documents/${document.id}/photo-items/${item.id}/`, { reason: "用户从照片文书移除" }, { revision: document.revision, idempotencyKey: createIdempotencyKey("remove-document-photo") }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  const thumbnailUrl = safeDownloadUrl(item.thumbnail_url);
  return (
    <article className="photo-item">
      <div className="photo-item-preview">{thumbnailUrl ? <img src={thumbnailUrl} alt={`照片 ${item.sequence + 1}`} /> : <span>照片 {item.sequence + 1}</span>}<small>第 {item.sequence + 1} 页</small></div>
      <div className="photo-item-fields">
        <label className="field"><span>反映内容 <small>{item.reflected_content_origin === "manual" ? "人工内容" : "AI 初稿"}</small></span><textarea rows={3} value={reflectedContent} onChange={(event) => setReflectedContent(event.target.value)} /></label>
        <label className="field"><span>说明 <small>{item.description_origin === "manual" ? "人工内容" : "AI 初稿"}</small></span><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label>
        {mutation.isError || removeMutation.isError ? <div className="notice notice-error">{getErrorMessage(mutation.error ?? removeMutation.error)}</div> : null}
        <div className="form-actions compact"><button className="button button-danger-outline button-small" type="button" disabled={removeMutation.isPending} onClick={() => removeMutation.mutate()}>移除</button><button className="button button-primary button-small" type="button" disabled={mutation.isPending || (reflectedContent === (item.reflected_content ?? "") && description === (item.description ?? ""))} onClick={() => mutation.mutate()}>保存逐图内容</button></div>
      </div>
    </article>
  );
}

function QaItemsEditor({ document }: { document: InspectionDocument }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [newRole, setNewRole] = useState<"" | "question" | "answer">("");
  const [newText, setNewText] = useState("");
  const refresh = async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] });
  const syncMutation = useMutation({
    mutationFn: () => api.post(`/documents/${document.id}/qa-items/sync/`, {}, { revision: document.revision }),
    onSuccess: refresh,
  });
  const addMutation = useMutation({
    mutationFn: () => api.post(
      `/documents/${document.id}/qa-items/`,
      { text: newText.trim(), confirmed_role: newRole || null },
      { revision: document.revision },
    ),
    onSuccess: async () => {
      setAdding(false);
      setNewRole("");
      setNewText("");
      await refresh();
    },
  });
  return (
    <div className="document-repeating-section">
      <div className="subsection-heading"><div><h3>问答正文</h3><p>每段最终正文使用人工确认角色与当前文字；AI 建议不等于声学身份。转写稍后到达时由你显式同步。</p></div><div className="form-actions compact"><button className="button button-secondary button-small" type="button" disabled={syncMutation.isPending} onClick={() => syncMutation.mutate()}>同步转写候选</button><button className="button button-secondary button-small" type="button" onClick={() => setAdding((value) => !value)}>新增问答</button></div></div>
      {adding ? <div className="qa-add-form"><label>新增角色<select aria-label="新增角色" value={newRole} onChange={(event) => setNewRole(event.target.value as typeof newRole)}><option value="">未确认</option><option value="question">问</option><option value="answer">答</option></select></label><label>新增正文<textarea aria-label="新增正文" rows={3} value={newText} onChange={(event) => setNewText(event.target.value)} /></label><button className="button button-primary button-small" type="button" disabled={!newText.trim() || addMutation.isPending} onClick={() => addMutation.mutate()}>添加问答</button></div> : null}
      {syncMutation.isError || addMutation.isError ? <div className="notice notice-error">{getErrorMessage(syncMutation.error ?? addMutation.error)}</div> : null}
      {(document.qa_items ?? []).length === 0 ? <EmptyState title="尚未生成问答候选" description="先确认转写片段与语义角色，再点击“同步转写候选”；也可以人工新增。" /> : <div className="qa-item-list">{document.qa_items!.map((item, index, items) => <QaItem key={item.id} item={item} document={document} index={index} itemIds={items.map((value) => value.id)} />)}</div>}
    </div>
  );
}

function QaItem({ item, document, index, itemIds }: { item: NonNullable<InspectionDocument["qa_items"]>[number]; document: InspectionDocument; index: number; itemIds: string[] }) {
  const queryClient = useQueryClient();
  const [role, setRole] = useState(item.confirmed_role ?? "");
  const [text, setText] = useState(item.text);
  useEffect(() => {
    setRole(item.confirmed_role ?? "");
    setText(item.text);
  }, [item.confirmed_role, item.text]);
  const mutation = useMutation({
    mutationFn: () => api.patch(`/documents/${document.id}/qa-items/${item.id}/`, { confirmed_role: role || null, text, content_origin: "manual" }, { revision: document.revision }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  const reorderMutation = useMutation({
    mutationFn: (direction: -1 | 1) => {
      const reordered = [...itemIds];
      const destination = index + direction;
      [reordered[index], reordered[destination]] = [reordered[destination], reordered[index]];
      return api.put(`/documents/${document.id}/qa-items/`, { item_ids: reordered }, { revision: document.revision });
    },
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/documents/${document.id}/qa-items/${item.id}/`, {}, { revision: document.revision }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["document", document.id] }),
  });
  return (
    <article className="qa-item">
      <div className="qa-sequence">{item.sequence}</div>
      <label className="field qa-role"><span>角色</span><select value={role} onChange={(event) => setRole(event.target.value as "" | "question" | "answer")}><option value="">未确认</option><option value="question">问</option><option value="answer">答</option></select><small>AI 建议：{item.suggested_role === "question" ? "问" : item.suggested_role === "answer" ? "答" : "未知"}</small></label>
      <label className="field qa-text"><span>正文 <small>{item.content_origin === "manual" ? "人工内容" : "AI 初稿"}</small></span><textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} /></label>
      <div className="qa-item-action"><button className="button button-ghost button-small" aria-label="上移" type="button" disabled={index === 0 || reorderMutation.isPending} onClick={() => reorderMutation.mutate(-1)}>上移</button><button className="button button-ghost button-small" aria-label="下移" type="button" disabled={index === itemIds.length - 1 || reorderMutation.isPending} onClick={() => reorderMutation.mutate(1)}>下移</button><button className="button button-danger-outline button-small" type="button" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>删除问答</button><button className="button button-secondary button-small" type="button" disabled={!role || !text.trim() || mutation.isPending || (role === (item.confirmed_role ?? "") && text === item.text)} onClick={() => mutation.mutate()}>保存</button>{mutation.isError || reorderMutation.isError || deleteMutation.isError ? <small className="form-error">{getErrorMessage(mutation.error ?? reorderMutation.error ?? deleteMutation.error)}</small> : null}</div>
    </article>
  );
}

export function DocumentPanel({ inspectionId, type }: WorkspacePanelProps & { type: DocumentType }) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [generateOpen, setGenerateOpen] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [name, setName] = useState("");

  const inspectionQuery = useQuery({ queryKey: ["inspection", inspectionId], queryFn: () => api.get<Inspection>(`/inspections/${inspectionId}/`) });
  const documentsQuery = useQuery({
    queryKey: ["documents", inspectionId, type],
    queryFn: () => api.get<InspectionDocument[] | PaginatedResponse<InspectionDocument>>(`/inspections/${inspectionId}/documents/?document_type=${type}`),
  });
  const summary = asList(documentsQuery.data).find((document) => document.document_type === type) ?? asList(documentsQuery.data)[0];
  const documentQuery = useQuery({
    queryKey: ["document", summary?.id],
    queryFn: () => api.get<InspectionDocument>(`/documents/${summary!.id}/`),
    enabled: Boolean(summary?.id),
    refetchInterval: (query) => documentGenerationPollInterval(query.state.data?.generator_status),
  });
  const document = documentQuery.data ?? summary;

  useEffect(() => {
    if (document) setName(document.name);
  }, [document?.id, document?.name]);

  const createMutation = useMutation({
    mutationFn: () => api.post<InspectionDocument>(`/inspections/${inspectionId}/documents/`, { document_type: type }, { idempotencyKey: createIdempotencyKey(`create-${type}-document`) }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["documents", inspectionId, type] }),
  });
  const generateMutation = useMutation({
    mutationFn: () => api.post(`/documents/${document!.id}/generate/`, { expected_revision: document?.revision, preserve_manual: true }, { revision: document?.revision, idempotencyKey: createIdempotencyKey(`generate-${type}`) }),
    onSuccess: async () => {
      setGenerateOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["document", document?.id] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId, type] });
    },
  });
  const renameMutation = useMutation({
    mutationFn: () => api.patch(`/documents/${document!.id}/`, { name: name.trim(), expected_revision: document?.revision }, { revision: document?.revision }),
    onSuccess: async () => {
      setEditingName(false);
      await queryClient.invalidateQueries({ queryKey: ["document", document?.id] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId, type] });
    },
  });
  const retryMutation = useMutation({
    mutationFn: () => api.post(`/jobs/${document!.generator_job_id}/retry/`, {}, { idempotencyKey: createIdempotencyKey("retry-document") }),
    onSuccess: async () => documentQuery.refetch(),
  });
  const confirmMutation = useMutation({
    mutationFn: () => api.post(`/documents/${document!.id}/confirm/`, { expected_revision: document?.revision }, { revision: document?.revision, idempotencyKey: createIdempotencyKey(`confirm-${type}`) }),
    onSuccess: async () => {
      setConfirmOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["document", document?.id] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId, type] });
    },
  });
  const exportMutation = useMutation({
    mutationFn: () => api.post<Blob | { download_url?: string; export?: { download_url?: string } }>(`/documents/${document!.id}/export/`, { expected_revision: document?.revision }, { revision: document?.revision, idempotencyKey: createIdempotencyKey(`export-${type}`) }),
    onSuccess: (response) => {
      if (response instanceof Blob) {
        downloadBlob(response, `${document?.name || DOCUMENT_LABELS[type]}.docx`);
        return;
      }
      const url = safeDownloadUrl(response.download_url ?? response.export?.download_url);
      if (url) {
        const anchor = window.document.createElement("a");
        anchor.href = url;
        anchor.rel = "noreferrer";
        anchor.click();
      }
    },
  });

  if (documentsQuery.isLoading || inspectionQuery.isLoading) return <LoadingState label={`正在加载${DOCUMENT_LABELS[type]}`} />;
  if (documentsQuery.isError || inspectionQuery.isError) return <ErrorState message={getErrorMessage(documentsQuery.error ?? inspectionQuery.error)} onRetry={() => void Promise.all([documentsQuery.refetch(), inspectionQuery.refetch()])} />;

  if (!document) {
    return (
      <section aria-labelledby="document-title">
        <div className="section-heading"><div><p className="eyebrow">Word 初稿</p><h2 id="document-title">{DOCUMENT_LABELS[type]}</h2><p>文书从原始 Word 模板生成，保留模板版式并由检查人员最终确认。</p></div></div>
        <EmptyState title={`尚未建立${DOCUMENT_LABELS[type]}`} description={`新建后默认命名为“${inspectionQuery.data?.unit_name ?? "被检查单位"} + ${DOCUMENT_LABELS[type]}”，可继续编辑字段并生成初稿。`} action={<button className="button button-primary" type="button" disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>{createMutation.isPending ? "正在建立…" : `新建${DOCUMENT_LABELS[type]}`}</button>} />
        {createMutation.isError ? <ErrorState message={getErrorMessage(createMutation.error)} /> : null}
      </section>
    );
  }

  const fields = normalizeFields(document.fields).filter((field) => !field.shared);
  const inspection = inspectionQuery.data!;
  const busyGeneration = document.generator_status === "queued" || document.generator_status === "running";

  return (
    <section aria-labelledby="document-title">
      <div className="section-heading document-heading">
        <div><p className="eyebrow">Word 初稿</p><h2 id="document-title">{DOCUMENT_LABELS[type]}</h2>{editingName ? <div className="document-name-editor"><label className="sr-only" htmlFor={`document-name-${document.id}`}>文书名称</label><input id={`document-name-${document.id}`} value={name} onChange={(event) => setName(event.target.value)} maxLength={300} /><button className="button button-primary button-small" type="button" disabled={!name.trim() || name.trim() === document.name || renameMutation.isPending} onClick={() => renameMutation.mutate()}>保存名称</button><button className="button button-ghost button-small" type="button" onClick={() => { setName(document.name); setEditingName(false); }}>取消</button></div> : <p data-assistant-source-id={document.id} data-assistant-revision={document.revision}>{document.name} <button className="text-button" type="button" onClick={() => setEditingName(true)}>修改名称</button></p>}{renameMutation.isError ? <small className="form-error">{getErrorMessage(renameMutation.error)}</small> : null}</div>
        <div className="document-actions">
          <StatusPill status={document.state} />
          {document.generator_status !== "failed" ? <button className="button button-secondary" type="button" disabled={document.state === "confirmed" || busyGeneration || generateMutation.isPending} onClick={() => setGenerateOpen(true)}>{busyGeneration || generateMutation.isPending ? "正在生成…" : "生成 / 重新生成初稿"}</button> : null}
          <button className="button button-primary" type="button" disabled={document.state === "confirmed" || busyGeneration || !document.generation_valid || document.generator_status !== "succeeded"} onClick={() => setConfirmOpen(true)}>确认文书</button>
          <button className="button button-secondary" type="button" disabled={document.state !== "confirmed" || exportMutation.isPending || busyGeneration} onClick={() => exportMutation.mutate()}>{exportMutation.isPending ? "正在导出…" : "导出 DOCX"}</button>
        </div>
      </div>

      {document.invalidation_reason ? <div className="notice notice-warning"><strong>文书已退回初稿：</strong>{document.invalidation_reason}</div> : null}
      {document.state === "draft" && !document.generation_valid && document.generator_status !== "failed" ? <div className="notice notice-info">当前修订尚未成功生成，完成生成并核对后才能确认；只有已确认文书可以导出。</div> : null}
      {document.state === "confirmed" ? <div className="notice notice-success">此文书已由用户确认。后台任务不会静默重新生成；相关输入变化时会同步退回初稿并记录原因。</div> : null}
      {document.upgrade_available ? <div className="notice notice-info">模板识别或生成规则已有新版本。你可以主动重新处理当前初稿；已确认文书不会自动升级。</div> : null}
      {document.generator_status ? <div className="generation-status"><StatusPill status={document.generator_status} /><span>{document.generator_status === "failed" ? document.generator_safe_error || document.safe_error_message || "生成失败，上一份成功产物仍然保留。" : document.generator_status === "superseded" ? "生成期间输入已变化，本次结果没有写回。" : "文书生成作为独立处理步骤运行。"}</span>{document.generator_status === "failed" && document.generator_job_id ? <button className="button button-secondary button-small" type="button" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate()}>仅重试生成</button> : null}</div> : null}

      <section className="panel shared-fields-card">
        <div className="subsection-heading"><div><h3>检查共享字段</h3><p>这里只有一个当前来源。在文书页面修改时，实际写回检查并使三类已确认文书退回初稿。</p></div><span className="shared-badge">检查共享字段</span></div>
        <dl className="shared-field-grid">
          <div><dt>单位名称</dt><dd>{inspection.unit_name}</dd></div><div><dt>地址</dt><dd>{inspection.unit_address}</dd></div><div><dt>负责人</dt><dd>{inspection.responsible_person || "待补充"}</dd></div><div><dt>联系人及电话</dt><dd>{[inspection.contact_name, inspection.contact_phone].filter(Boolean).join(" · ") || "待补充"}</dd></div><div><dt>检查时间</dt><dd>{formatDateTime(inspection.inspection_at)}</dd></div><div><dt>检查类型</dt><dd>{inspectionTypeLabel(inspection.inspection_type)}</dd></div>
        </dl>
        <p className="muted">需要修改时请使用工作台顶部“编辑共享信息”。</p>
      </section>

      <section className="panel document-fields-card">
        <div className="subsection-heading"><div><h3>文书专属字段</h3><p>人工编辑内容会标记来源并锁定；重新生成默认保留，不会静默覆盖。</p></div></div>
        {fields.length === 0 ? <EmptyState title="还没有专属字段" description="生成初稿后会显示 AI 或系统填充内容；也可以由模板映射定义字段。" /> : <div className="document-field-grid">{fields.map((field) => <FieldEditor key={field.key} document={document} field={field} inspectionId={inspectionId} />)}</div>}
      </section>

      <FactDependenciesEditor document={document} inspectionId={inspectionId} />

      {type === "photo" ? <PhotoItemsEditor document={document} inspectionId={inspectionId} /> : null}
      {type === "interview" ? <QaItemsEditor document={document} /> : null}

      {(generateMutation.isError || confirmMutation.isError || exportMutation.isError || retryMutation.isError) ? <div className="notice notice-error" role="alert">{getErrorMessage(generateMutation.error ?? confirmMutation.error ?? exportMutation.error ?? retryMutation.error)}</div> : null}

      <ConfirmDialog
        open={generateOpen}
        title={document.generator_status === "succeeded" ? "重新生成这份文书初稿？" : "生成这份文书初稿？"}
        description={<><p>系统会使用当前已确认事实、共享字段、选图或问答候选生成初稿。</p><p><strong>人工编辑并锁定的字段会默认保留，不会被静默覆盖。</strong>如需改写人工内容，请先在对应字段中明确修改。</p></>}
        confirmLabel="保留人工内容并生成"
        busy={generateMutation.isPending}
        onCancel={() => setGenerateOpen(false)}
        onConfirm={() => generateMutation.mutate()}
      />

      <ConfirmDialog
        open={confirmOpen}
        title={`确认${DOCUMENT_LABELS[type]}？`}
        description={<><p>请先核对所有字段、事实依据、照片或问答正文。确认代表人工审核完成，但不会自动提交到其他政务系统。</p><p>之后相关输入有效变化时，文书会立即退回初稿并写入历史。</p></>}
        confirmLabel="确认文书"
        busy={confirmMutation.isPending}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => confirmMutation.mutate()}
      />
    </section>
  );
}
