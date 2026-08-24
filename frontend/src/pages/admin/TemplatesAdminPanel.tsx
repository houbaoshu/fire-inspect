import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import { asList, type DocumentType, type JobStatus, type JobSummary, type PaginatedResponse } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { formatDateTime } from "../../utils/format";

interface TemplateRegion {
  id: string;
  display_name: string;
  repeat_scope: string;
  fill_strategy: string;
  confidence: number;
  enabled: boolean;
  required?: boolean;
  location_preview?: string;
  warning?: string;
}

interface WordTemplate {
  id: string;
  name: string;
  document_type: DocumentType;
  active: boolean;
  sha256?: string;
  created_at?: string;
  recognition_status?: JobStatus;
  safe_error_message?: string;
  recognition_job?: JobSummary | null;
  mapping?: {
    id: string;
    revision_no: number;
    state: "draft" | "confirmed" | "superseded";
    regions: TemplateRegion[];
    warnings?: string[];
  };
}

const DOCUMENT_LABELS: Record<DocumentType, string> = { record: "监督检查记录", photo: "照片文书", interview: "询问笔录" };
const STRATEGY_LABELS: Record<string, string> = { label_suffix: "标签后填充", table_cell: "表格单元格", checkbox: "复选框", image_region: "图片区域", body: "正文区域", footer_field: "页脚字段", preserve_blank: "保持空白" };
const REPEAT_SCOPE_LABELS: Record<string, string> = { single: "单一区域", document_common: "文书公共字段", per_item: "逐项重复" };

function RegionRow({ template, region, editable }: { template: WordTemplate; region: TemplateRegion; editable: boolean }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(region.display_name);
  const [strategy, setStrategy] = useState(region.fill_strategy);
  const [repeatScope, setRepeatScope] = useState(region.repeat_scope);
  const [enabled, setEnabled] = useState(region.enabled);
  const [required, setRequired] = useState(Boolean(region.required));
  useEffect(() => { setName(region.display_name); setStrategy(region.fill_strategy); setRepeatScope(region.repeat_scope); setEnabled(region.enabled); setRequired(Boolean(region.required)); }, [region.display_name, region.enabled, region.fill_strategy, region.repeat_scope, region.required]);
  const mutation = useMutation({
    mutationFn: () => api.patch(`/templates/${template.id}/mapping/regions/${region.id}/`, { display_name: name.trim(), fill_strategy: strategy, repeat_scope: repeatScope, enabled, required }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["template", template.id] }),
  });
  const confidence = region.confidence > 1 ? region.confidence : region.confidence * 100;
  return (
    <tr>
      <td><input className="table-input" aria-label="业务名称" value={name} disabled={!editable} onChange={(event) => setName(event.target.value)} /><small>{region.location_preview || "模板中的已识别区域"}</small>{region.warning ? <small className="form-error">{region.warning}</small> : null}{mutation.isError ? <small className="form-error">{getErrorMessage(mutation.error)}</small> : null}</td>
      <td><select aria-label={`${region.display_name} 填充方式`} value={strategy} disabled={!editable} onChange={(event) => setStrategy(event.target.value)}>{Object.entries(STRATEGY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
      <td><select aria-label={`${region.display_name} 重复范围`} value={repeatScope} disabled={!editable} onChange={(event) => setRepeatScope(event.target.value)}>{Object.entries(REPEAT_SCOPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></td>
      <td><span className={`confidence confidence-${confidence >= 80 ? "high" : confidence >= 55 ? "medium" : "low"}`}>{Math.round(confidence)}%</span></td>
      <td><label className="switch"><input type="checkbox" checked={enabled} disabled={!editable} onChange={(event) => setEnabled(event.target.checked)} /><span>{enabled ? "启用" : "禁用"}</span></label><label className="switch"><input type="checkbox" checked={required} disabled={!editable} onChange={(event) => setRequired(event.target.checked)} /><span>{required ? "必填" : "可选"}</span></label></td>
      <td>{editable ? <button className="button button-secondary button-small" type="button" disabled={!name.trim() || mutation.isPending || (name === region.display_name && strategy === region.fill_strategy && repeatScope === region.repeat_scope && enabled === region.enabled && required === Boolean(region.required))} onClick={() => mutation.mutate()}>保存</button> : <span className="muted">只读</span>}</td>
    </tr>
  );
}

export function TemplatesAdminPanel() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [type, setType] = useState<DocumentType>("record");
  const [fileError, setFileError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [addRegionOpen, setAddRegionOpen] = useState(false);
  const [regionName, setRegionName] = useState("");
  const [regionAnchor, setRegionAnchor] = useState("");
  const [regionStrategy, setRegionStrategy] = useState("label_suffix");
  const [regionRepeatScope, setRegionRepeatScope] = useState("single");
  const [regionRequired, setRegionRequired] = useState(false);

  const templatesQuery = useQuery({ queryKey: ["templates"], queryFn: () => api.get<WordTemplate[] | PaginatedResponse<WordTemplate>>("/templates/"), refetchInterval: (query) => asList(query.state.data as WordTemplate[] | PaginatedResponse<WordTemplate> | undefined).some((template) => { const status = template.recognition_job?.status ?? template.recognition_status; return status === "queued" || status === "running"; }) ? 3000 : false });
  const templates = asList(templatesQuery.data);
  const selectedSummary = templates.find((template) => template.id === selectedId) ?? templates[0];
  const templateQuery = useQuery({ queryKey: ["template", selectedSummary?.id], queryFn: () => api.get<WordTemplate>(`/templates/${selectedSummary!.id}/`), enabled: Boolean(selectedSummary?.id), refetchInterval: (query) => { const status = query.state.data?.recognition_job?.status ?? query.state.data?.recognition_status; return status === "queued" || status === "running" ? 3000 : false; } });
  const selected = templateQuery.data ?? selectedSummary;

  const uploadMutation = useMutation({
    mutationFn: () => { if (!file) throw new Error("NO_FILE"); const form = new FormData(); form.append("file", file, file.name); form.append("name", name.trim()); form.append("document_type", type); return api.post("/templates/", form, { idempotencyKey: createIdempotencyKey("upload-template") }); },
    onSuccess: async () => { setFile(null); setName(""); setUploadOpen(false); await queryClient.invalidateQueries({ queryKey: ["templates"] }); },
  });
  const confirmMutation = useMutation({
    mutationFn: () => api.post(`/templates/${selected!.id}/mapping/confirm/`, {}, { idempotencyKey: createIdempotencyKey("confirm-template-mapping") }),
    onSuccess: async () => { setConfirmOpen(false); await queryClient.invalidateQueries({ queryKey: ["template", selected?.id] }); },
  });
  const recognizeMutation = useMutation({
    mutationFn: () => api.post(`/templates/${selected!.id}/recognize/`, {}, { idempotencyKey: createIdempotencyKey("recognize-template") }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["template", selected?.id] }),
  });
  const reviseMutation = useMutation({
    mutationFn: () => api.post(`/templates/${selected!.id}/mapping/revise/`, {}, { idempotencyKey: createIdempotencyKey("revise-template-mapping") }),
    onSuccess: async () => {
      setAddRegionOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["template", selected?.id] });
    },
  });
  const addRegionMutation = useMutation({
    mutationFn: () => {
      return api.post(`/templates/${selected!.id}/mapping/regions/`, {
        display_name: regionName.trim(),
        repeat_scope: regionRepeatScope,
        fill_strategy: regionStrategy,
        enabled: true,
        required: regionRequired,
        location_preview: regionAnchor.trim(),
        anchor: regionAnchor.trim(),
      }, { idempotencyKey: createIdempotencyKey("add-template-region") });
    },
    onSuccess: async () => {
      setRegionName("");
      setRegionAnchor("");
      setRegionStrategy("label_suffix");
      setRegionRepeatScope("single");
      setRegionRequired(false);
      setAddRegionOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["template", selected?.id] });
    },
  });
  const chooseFile = (candidate?: File) => { if (!candidate) return setFile(null); if (!candidate.name.toLowerCase().endsWith(".docx")) { setFileError("Word 模板仅支持不含宏的 DOCX 文件。"); setFile(null); return; } setFileError(null); setFile(candidate); if (!name) setName(candidate.name.replace(/\.docx$/i, "")); };
  const submit = (event: FormEvent) => { event.preventDefault(); if (file && name.trim()) uploadMutation.mutate(); };

  if (templatesQuery.isLoading) return <LoadingState label="正在加载 Word 模板" />;
  if (templatesQuery.isError) return <ErrorState message={getErrorMessage(templatesQuery.error)} onRetry={() => void templatesQuery.refetch()} />;
  return (
    <section aria-labelledby="templates-admin-title">
      <div className="section-heading"><div><h2 id="templates-admin-title">Word 模板与映射</h2><p>原始模板版本不可变；系统识别中文业务区域，管理员确认后复用。</p></div><button className="button button-primary" type="button" onClick={() => setUploadOpen((value) => !value)}>＋ 上传 DOCX 模板</button></div>
      <div className="notice notice-info">界面只显示稳定中文业务名、位置预览、填充方式与置信度，不暴露哈希或内部定位键。中低置信区域必须人工修正或禁用。</div>
      {uploadOpen ? <form className="panel inline-create-panel" onSubmit={submit}><div className="form-grid two-columns"><label className="field field-wide"><span>DOCX 文件</span><input type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => chooseFile(event.target.files?.[0])} required />{fileError ? <small className="form-error">{fileError}</small> : null}<small>不要求预先添加占位符；系统不会修改上传原件。</small></label><label className="field"><span>模板名称</span><input value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} /></label><label className="field"><span>文书类型</span><select value={type} onChange={(event) => setType(event.target.value as DocumentType)}>{Object.entries(DOCUMENT_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>{uploadMutation.isError ? <div className="notice notice-error">{getErrorMessage(uploadMutation.error)}</div> : null}<div className="form-actions compact"><button className="button button-ghost" type="button" onClick={() => setUploadOpen(false)}>取消</button><button className="button button-primary" type="submit" disabled={!file || !name.trim() || uploadMutation.isPending}>上传并排队识别</button></div></form> : null}
      {templates.length === 0 ? <EmptyState title="还没有模板" description="初始化会预置三份权威模板，也可以上传新的 DOCX 模板并人工确认映射。" /> : <div className="template-admin-layout"><nav className="panel template-list" aria-label="Word 模板列表">{templates.map((template) => { const recognitionStatus = template.recognition_job?.status ?? template.recognition_status; return <button className={selected?.id === template.id ? "active" : ""} key={template.id} type="button" onClick={() => setSelectedId(template.id)}><span className="template-icon" aria-hidden="true">W</span><span><strong>{template.name}</strong><small>{DOCUMENT_LABELS[template.document_type]} · {template.mapping?.state === "confirmed" ? "映射已确认" : "映射待确认"}</small></span>{recognitionStatus ? <StatusPill status={recognitionStatus} /> : null}</button>; })}</nav>{selected ? <div className="template-detail"><section className="panel"><div className="template-detail-header"><div><p className="eyebrow">{DOCUMENT_LABELS[selected.document_type]}</p><h3>{selected.name}</h3><p>上传于 {formatDateTime(selected.created_at)} · 原始版本保持不变</p></div><div>{selected.mapping ? <StatusPill status={selected.mapping.state === "confirmed" ? "confirmed" : "draft"} label={selected.mapping.state === "confirmed" ? `映射 v${selected.mapping.revision_no} 已确认` : `映射 v${selected.mapping.revision_no} 草稿`} /> : null}</div></div>{selected.mapping?.warnings?.map((warning, index) => <div className="notice notice-warning" key={index}>{warning}</div>)}{selected.recognition_job?.safe_error_message || selected.safe_error_message ? <div className="notice notice-error">{selected.recognition_job?.safe_error_message || selected.safe_error_message}</div> : null}{(selected.recognition_job?.status ?? selected.recognition_status) === "failed" ? <button className="button button-secondary" type="button" onClick={() => recognizeMutation.mutate()}>仅重试模板识别</button> : null}{recognizeMutation.isError ? <div className="notice notice-error">{getErrorMessage(recognizeMutation.error)}</div> : null}</section><section className="panel mapping-regions"><div className="subsection-heading"><div><h3>业务区域映射</h3><p>确认前可改名、修改填充方式、重复范围、必填状态、禁用或补充；签名区必须保持空白。</p></div><div className="mapping-actions">{selected.mapping?.state === "draft" ? <><button className="button button-secondary" type="button" onClick={() => setAddRegionOpen((value) => !value)}>＋ 补充业务区域</button><button className="button button-primary" type="button" onClick={() => setConfirmOpen(true)}>确认映射</button></> : selected.mapping?.state === "confirmed" ? <button className="button button-secondary" type="button" disabled={reviseMutation.isPending} onClick={() => reviseMutation.mutate()}>{reviseMutation.isPending ? "正在创建…" : "创建新映射草稿"}</button> : null}</div></div>{reviseMutation.isError ? <div className="notice notice-error">{getErrorMessage(reviseMutation.error)}</div> : null}{addRegionOpen && selected.mapping?.state === "draft" ? <form className="template-region-form" onSubmit={(event) => { event.preventDefault(); addRegionMutation.mutate(); }}><div className="form-grid two-columns"><label className="field"><span>中文业务名称</span><input value={regionName} onChange={(event) => setRegionName(event.target.value)} maxLength={100} required /></label><label className="field"><span>模板固定标签或位置描述</span><input value={regionAnchor} onChange={(event) => setRegionAnchor(event.target.value)} maxLength={160} required placeholder="例如：联系方式：标签后的空白区域" /></label><label className="field"><span>填充方式</span><select value={regionStrategy} onChange={(event) => setRegionStrategy(event.target.value)}>{Object.entries(STRATEGY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field"><span>重复范围</span><select value={regionRepeatScope} onChange={(event) => setRegionRepeatScope(event.target.value)}>{Object.entries(REPEAT_SCOPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="checkbox-field field-wide"><input type="checkbox" checked={regionRequired} onChange={(event) => setRegionRequired(event.target.checked)} /><span>确认映射前必须启用并定位此区域</span></label></div>{addRegionMutation.isError ? <div className="notice notice-error">{getErrorMessage(addRegionMutation.error)}</div> : null}<div className="form-actions compact"><button className="button button-ghost" type="button" onClick={() => setAddRegionOpen(false)}>取消</button><button className="button button-primary" type="submit" disabled={!regionName.trim() || !regionAnchor.trim() || addRegionMutation.isPending}>保存补充区域</button></div></form> : null}{!selected.mapping ? <EmptyState title="尚无识别结果" description="识别完成后将显示稳定中文业务区域。" /> : <div className="table-wrap"><table><thead><tr><th>业务名称与位置</th><th>填充方式</th><th>重复范围</th><th>置信度</th><th>状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{selected.mapping.regions.map((region) => <RegionRow key={region.id} template={selected} region={region} editable={selected.mapping?.state === "draft"} />)}</tbody></table></div>}</section></div> : null}</div>}
      <ConfirmDialog open={confirmOpen} title="确认这版模板映射？" description={<><p>确认后该映射版本不可原位编辑；后续修改将克隆为新草稿。</p><p>映射变化只会使实际使用该映射的已确认文书退回初稿，并记录原因。</p>{confirmMutation.isError ? <span className="form-error">{getErrorMessage(confirmMutation.error)}</span> : null}</>} confirmLabel="确认映射版本" busy={confirmMutation.isPending} onCancel={() => setConfirmOpen(false)} onConfirm={() => confirmMutation.mutate()} />
    </section>
  );
}
