import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import { asList, type JobStatus, type JobSummary, type PaginatedResponse } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { formatDateTime } from "../../utils/format";

interface KnowledgeDocument {
  id: string;
  title: string;
  category: "law" | "standard" | "local_rule" | "internal" | "other";
  issuer?: string;
  source_reference?: string;
  version?: string;
  publication_date?: string;
  source_verification: "pending" | "verified" | "rejected";
  validity: "unknown" | "effective" | "expired";
  processing_status?: JobStatus | "pending";
  processing_job?: JobSummary | null;
  safe_error_message?: string;
  revision?: number;
  clause_count?: number;
  created_at?: string;
}

const CATEGORY_LABELS: Record<KnowledgeDocument["category"], string> = {
  law: "正式法律法规",
  standard: "标准规范",
  local_rule: "地方规定",
  internal: "内部制度",
  other: "其他材料",
};

export function KnowledgeAdminPanel() {
  const queryClient = useQueryClient();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState<KnowledgeDocument["category"]>("other");
  const [issuer, setIssuer] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [version, setVersion] = useState("");
  const [publicationDate, setPublicationDate] = useState("");
  const [declaredInternal, setDeclaredInternal] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null);
  const [deleteReason, setDeleteReason] = useState("");

  const documentsQuery = useQuery({
    queryKey: ["knowledge-documents"],
    queryFn: () => api.get<KnowledgeDocument[] | PaginatedResponse<KnowledgeDocument>>("/knowledge/documents/"),
    refetchInterval: (query) => asList(query.state.data as KnowledgeDocument[] | PaginatedResponse<KnowledgeDocument> | undefined).some((document) => {
      const status = document.processing_job?.status ?? document.processing_status;
      return status === "pending" || status === "queued" || status === "running";
    }) ? 3000 : false,
  });
  const documents = asList(documentsQuery.data);

  const uploadMutation = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("NO_FILE");
      const formData = new FormData();
      formData.append("file", file, file.name);
      formData.append("title", title.trim());
      formData.append("category", category);
      formData.append("declared_internal", String(declaredInternal));
      formData.append("issuer", issuer.trim());
      formData.append("source_reference", sourceReference.trim());
      formData.append("version", version.trim());
      if (publicationDate) formData.append("publication_date", publicationDate);
      formData.append("source_verification", "pending");
      formData.append("validity", "unknown");
      return api.post("/knowledge/documents/", formData, { idempotencyKey: createIdempotencyKey("upload-knowledge") });
    },
    onSuccess: async () => {
      setFile(null); setTitle(""); setCategory("other"); setIssuer(""); setSourceReference(""); setVersion(""); setPublicationDate(""); setDeclaredInternal(false); setUploadOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/knowledge/documents/${deleteTarget!.id}/`, { confirm: true, reason: deleteReason.trim() }, { revision: deleteTarget?.revision, idempotencyKey: createIdempotencyKey("delete-knowledge"), }),
    onSuccess: async () => { setDeleteTarget(null); setDeleteReason(""); await queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] }); },
  });
  const retryMutation = useMutation({
    mutationFn: (id: string) => api.post(`/knowledge/documents/${id}/retry/`, {}, { idempotencyKey: createIdempotencyKey("retry-knowledge") }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["knowledge-documents"] }),
  });
  const chooseFile = (candidate: File | undefined) => {
    if (!candidate) { setFile(null); return; }
    const lower = candidate.name.toLowerCase();
    if (!lower.endsWith(".pdf") && !lower.endsWith(".docx")) { setFileError("知识文档仅支持文本型 PDF 或 DOCX。"); setFile(null); return; }
    setFileError(null); setFile(candidate); if (!title) setTitle(candidate.name.replace(/\.(pdf|docx)$/i, ""));
  };
  const submit = (event: FormEvent) => { event.preventDefault(); if (file && title.trim()) uploadMutation.mutate(); };

  if (documentsQuery.isLoading) return <LoadingState label="正在加载知识文档" />;
  if (documentsQuery.isError) return <ErrorState message={getErrorMessage(documentsQuery.error)} onRetry={() => void documentsQuery.refetch()} />;
  return (
    <section aria-labelledby="knowledge-admin-title">
      <div className="section-heading"><div><h2 id="knowledge-admin-title">知识文档</h2><p>维护法规、标准、地方规定、内部制度和其他材料；原始文档不可变保存。</p></div><button className="button button-primary" type="button" onClick={() => setUploadOpen((value) => !value)}>＋ 上传知识文档</button></div>
      <div className="notice notice-warning"><strong>有效性不是系统自动判断：</strong>缺少发布来源、版本或日期时默认“待核验 / 有效性未知”。测试 PDF 的存在不等于生产级法律有效证明。</div>
      {uploadOpen ? <form className="panel inline-create-panel" onSubmit={submit}><div className="form-grid two-columns"><label className="field field-wide"><span>PDF / DOCX 文件</span><input type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={(event) => chooseFile(event.target.files?.[0])} required />{fileError ? <small className="form-error">{fileError}</small> : null}</label><label className="field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={300} /></label><label className="field"><span>文档类别</span><select value={category} disabled={declaredInternal} onChange={(event) => setCategory(event.target.value as KnowledgeDocument["category"])}>{Object.entries(CATEGORY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><small>内部制度必须选择“内部制度”，不能标为正式法律法规。</small></label><label className="checkbox-field field-wide"><input type="checkbox" checked={declaredInternal} onChange={(event) => { const checked = event.target.checked; setDeclaredInternal(checked); if (checked) setCategory("internal"); }} /><span>我确认这份文件属于机构内部制度</span></label><label className="field"><span>发布来源</span><input value={issuer} onChange={(event) => setIssuer(event.target.value)} maxLength={300} /></label><label className="field"><span>正式来源参考</span><input value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} maxLength={500} placeholder="文件号或内网来源说明" /></label><label className="field"><span>版本</span><input value={version} onChange={(event) => setVersion(event.target.value)} maxLength={100} /></label><label className="field"><span>发布日期</span><input type="date" value={publicationDate} onChange={(event) => setPublicationDate(event.target.value)} /></label></div>{uploadMutation.isError ? <div className="notice notice-error">{getErrorMessage(uploadMutation.error)}</div> : null}<div className="form-actions compact"><button className="button button-ghost" type="button" onClick={() => setUploadOpen(false)}>取消</button><button className="button button-primary" type="submit" disabled={!file || !title.trim() || uploadMutation.isPending}>{uploadMutation.isPending ? "上传中…" : "上传并排队解析"}</button></div></form> : null}
      {documents.length === 0 ? <EmptyState title="还没有知识文档" description="上传文本型 PDF 或 DOCX 后，系统会在后台解析条款；扫描件 OCR 不是 MVP 必需能力。" /> : <div className="admin-card-grid">{documents.map((document) => { const status = document.processing_job?.status ?? (document.processing_status === "pending" ? "queued" : document.processing_status); const error = document.processing_job?.safe_error_message || document.safe_error_message; return <article className="panel admin-entity-card" key={document.id}><header><div><span className="entity-category">{CATEGORY_LABELS[document.category]}</span><h3>{document.title}</h3></div>{status ? <StatusPill status={status} label={!document.processing_job && document.processing_status === "pending" ? "待处理" : undefined} /> : null}</header>{error ? <div className="notice notice-error">{error}</div> : null}<dl><div><dt>来源核验</dt><dd className={document.source_verification === "verified" ? "verified-text" : "pending-text"}>{document.source_verification === "verified" ? "已核验" : document.source_verification === "rejected" ? "已驳回" : "待核验"}</dd></div><div><dt>有效状态</dt><dd>{document.validity === "effective" ? "有效" : document.validity === "expired" ? "已失效" : "未知"}</dd></div><div><dt>发布来源</dt><dd>{document.issuer || "待补充"}</dd></div><div><dt>条款数量</dt><dd>{document.clause_count ?? "—"}</dd></div></dl><footer><span>{formatDateTime(document.created_at)}</span><div>{status === "failed" ? <button className="button button-secondary button-small" type="button" onClick={() => retryMutation.mutate(document.id)}>仅重试解析</button> : null}<a className="button button-ghost button-small" href={`${api.baseUrl}/knowledge/documents/${document.id}/download/`} target="_blank" rel="noreferrer">原文件</a><button className="button button-danger-outline button-small" type="button" onClick={() => { setDeleteTarget(document); setDeleteReason(""); }}>删除</button></div></footer></article>; })}</div>}
      {retryMutation.isError ? <ErrorState message={getErrorMessage(retryMutation.error)} /> : null}
      <ConfirmDialog open={Boolean(deleteTarget)} title="删除知识文档？" description={<><p>将删除该文档的条款、索引和派生结果；原始删除操作会写入永久最小化审计。已关联法规证据的事实可能需要重新核实。</p>{deleteMutation.isError ? <span className="form-error">{getErrorMessage(deleteMutation.error)}</span> : null}</>} tone="danger" confirmLabel="确认删除" requireReason reason={deleteReason} onReasonChange={setDeleteReason} busy={deleteMutation.isPending} onCancel={() => setDeleteTarget(null)} onConfirm={() => deleteMutation.mutate()} />
    </section>
  );
}
