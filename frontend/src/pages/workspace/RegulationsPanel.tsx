import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { api, getErrorMessage } from "../../api/client";
import { asList, type KnowledgeSearchResult, type PaginatedResponse } from "../../api/types";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import type { WorkspacePanelProps } from "./workspace";

interface RawKnowledgeResult {
  id?: string;
  clause_id?: string;
  clause_identifier?: string;
  heading?: string;
  content?: string;
  original_text?: string;
  applicability_reason?: string;
  document_id?: string;
  document_title?: string;
  document?: KnowledgeSearchResult["document"];
  source_status?: string;
  source_verification?: string;
  page_start?: number;
}

interface KnowledgeResponse extends PaginatedResponse<RawKnowledgeResult> {
  found?: boolean;
  not_found?: boolean;
  query?: string;
  reason?: string;
  message?: string;
}

function sourceVerification(value?: string): "pending" | "verified" | "rejected" {
  if (value === "verified" || value === "已核验") return "verified";
  if (value === "rejected" || value === "已驳回" || value === "已拒绝") return "rejected";
  return "pending";
}

function normalizeResult(raw: RawKnowledgeResult, index = 0): KnowledgeSearchResult {
  const documentId = raw.document?.id || raw.document_id || "unknown-document";
  const clauseIdentifier = raw.clause_identifier || "相关原文";
  return {
    id: raw.id || raw.clause_id || `${documentId}-${clauseIdentifier}-${index}`,
    clause_identifier: raw.clause_identifier,
    heading: raw.heading,
    content: raw.content || raw.original_text || "",
    applicability_reason: raw.applicability_reason,
    document: raw.document || {
      id: documentId,
      title: raw.document_title || "来源文档",
      source_verification: sourceVerification(raw.source_verification || raw.source_status),
    },
    page_start: raw.page_start,
  };
}

function verificationLabel(value?: string) {
  return value === "verified" ? "来源已核验" : value === "rejected" ? "来源已驳回" : "待核验";
}

export function RegulationsPanel({ inspectionId: _inspectionId }: WorkspacePanelProps) {
  const [searchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[]>([]);
  const [searched, setSearched] = useState(false);
  const [notFoundReason, setNotFoundReason] = useState<string | null>(null);
  const selectedClauseId = searchParams.get("clause");
  const selectedDocumentId = searchParams.get("document");

  const directClauseQuery = useQuery({
    queryKey: ["knowledge-clause", selectedDocumentId, selectedClauseId],
    queryFn: () => api.get<RawKnowledgeResult>(`/knowledge/documents/${selectedDocumentId}/clauses/${selectedClauseId}/`),
    enabled: Boolean(selectedClauseId && selectedDocumentId),
  });

  useEffect(() => {
    if (directClauseQuery.data) {
      setResults([normalizeResult(directClauseQuery.data)]);
      setSearched(true);
      setNotFoundReason(null);
    }
  }, [directClauseQuery.data]);

  useEffect(() => {
    if (!selectedClauseId || results.length === 0) return;
    const handle = window.setTimeout(() => {
      const element = document.getElementById(`clause-${selectedClauseId}`);
      if (element && typeof element.scrollIntoView === "function") element.scrollIntoView({ block: "center" });
    }, 0);
    return () => window.clearTimeout(handle);
  }, [results, selectedClauseId]);

  const searchMutation = useMutation({
    mutationFn: () => api.get<RawKnowledgeResult[] | KnowledgeResponse>(`/knowledge/search/?q=${encodeURIComponent(query.trim())}`),
    onSuccess: (response) => {
      const items = asList(response).map((item, index) => normalizeResult(item, index));
      setResults(items);
      setSearched(true);
      const structured = Array.isArray(response) ? null : response;
      setNotFoundReason(items.length === 0 || structured?.not_found || structured?.found === false ? structured?.reason || structured?.message || "未找到达到可靠召回阈值的法规或业务知识。" : null);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (query.trim()) searchMutation.mutate();
  };

  return (
    <section aria-labelledby="regulations-title">
      <div className="section-heading">
        <div><p className="eyebrow">来源可回溯</p><h2 id="regulations-title">法规检索</h2><p>检索结果只解释已召回原文；没有可靠结果时系统明确显示“未找到”。</p></div>
      </div>

      <form className="regulation-search panel" onSubmit={submit}>
        <label className="search-field large"><span aria-hidden="true">⌕</span><span className="sr-only">输入法规问题、条款号或关键词</span><input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={500} placeholder="输入问题、条款号或关键词，例如：消防控制室值班要求" /></label>
        <button className="button button-primary" type="submit" disabled={!query.trim() || searchMutation.isPending}>{searchMutation.isPending ? "检索中…" : "检索法规"}</button>
      </form>

      <div className="notice notice-warning"><strong>来源状态说明：</strong>测试消防法文件及来源元数据不完整的文档显示“待核验”，不能据此认定为现行有效法律版本。</div>

      {directClauseQuery.isLoading || searchMutation.isPending ? <LoadingState label="正在检索条款原文" /> : null}
      {directClauseQuery.isError ? <ErrorState message={getErrorMessage(directClauseQuery.error)} onRetry={() => void directClauseQuery.refetch()} /> : null}
      {searchMutation.isError ? <ErrorState message={getErrorMessage(searchMutation.error)} onRetry={() => searchMutation.mutate()} /> : null}

      {!searched && !searchMutation.isPending && !directClauseQuery.isLoading ? (
        <div className="regulation-start-grid">
          <div className="panel regulation-tip"><span aria-hidden="true">条</span><h3>按条款定位</h3><p>输入“第十八条”等具体条款，查看保存的原文与文档来源。</p></div>
          <div className="panel regulation-tip"><span aria-hidden="true">问</span><h3>按问题检索</h3><p>用现场问题描述检索可能相关的法规，并人工判断适用性。</p></div>
          <div className="panel regulation-tip"><span aria-hidden="true">源</span><h3>回溯原文件</h3><p>每个结果保留原始文档入口和页码，不让模型编造来源。</p></div>
        </div>
      ) : null}

      {searched && results.length === 0 ? <EmptyState title="未找到可靠条款" description={notFoundReason || "请调整关键词或由管理员补充知识文档；系统不会生成不存在的条款。"} /> : null}

      {results.length > 0 ? (
        <div className="regulation-results">
          <div className="result-summary"><strong>找到 {results.length} 条可能相关的原文</strong><span>适用性仍需检查人员判断</span></div>
          {results.map((result) => {
            const focused = result.id === selectedClauseId;
            return (
              <article className={`panel regulation-result ${focused ? "focused" : ""}`} key={result.id} id={`clause-${result.id}`}>
                <header>
                  <div><span className="clause-id">{result.clause_identifier || "相关原文"}</span><h3>{result.heading || result.document.title}</h3></div>
                  <span className={`source-state source-${result.document.source_verification ?? "pending"}`}>{verificationLabel(result.document.source_verification)}</span>
                </header>
                <blockquote>{result.content}</blockquote>
                {result.applicability_reason ? <div className="applicability"><strong>可能适用理由</strong><p>{result.applicability_reason}</p></div> : null}
                <footer>
                  <div><strong>{result.document.title}</strong><span>{result.document.issuer || "发布来源待补充"}{result.page_start ? ` · 第 ${result.page_start} 页` : ""}</span></div>
                  <a className="button button-secondary button-small" href={`${api.baseUrl}/knowledge/documents/${encodeURIComponent(result.document.id)}/download/`} target="_blank" rel="noreferrer">打开原始文档</a>
                </footer>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}
