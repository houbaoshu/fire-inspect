import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import {
  asList,
  historyActor,
  historyNewValue,
  historyOldValue,
  type Fact,
  type FactEvidence,
  type HistoryEntry,
  type PaginatedResponse,
} from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { compactJson, formatDateTime, formatDuration } from "../../utils/format";
import type { WorkspacePanelProps } from "./workspace";

interface EvidenceCandidate {
  id: string;
  type: FactEvidence["type"];
  label: string;
  material_id?: string;
  start_ms?: number;
  end_ms?: number;
  clause_id?: string;
  document_id?: string;
}

function EvidenceLink({ evidence, inspectionId }: { evidence: FactEvidence; inspectionId: string }) {
  const label = evidence.label || evidence.clause_identifier || "打开证据";
  if (evidence.material_id) {
    const params = new URLSearchParams({ material: evidence.material_id });
    if (evidence.start_ms !== undefined) params.set("t", String(evidence.start_ms));
    return <Link className="evidence-chip" to={`/inspections/${inspectionId}/materials?${params}`}><span aria-hidden="true">▶</span>{label}{evidence.start_ms !== undefined ? ` · ${formatDuration(evidence.start_ms)}` : ""}</Link>;
  }
  if (evidence.document_id && evidence.clause_id) {
    const params = new URLSearchParams({ document: evidence.document_id, clause: evidence.clause_id });
    return <Link className="evidence-chip" to={`/inspections/${inspectionId}/regulations?${params}`}><span aria-hidden="true">法</span>{label}</Link>;
  }
  return <span className="evidence-chip"><span aria-hidden="true">证</span>{label}</span>;
}

function FactCard({ fact, inspectionId, focused }: { fact: Fact; inspectionId: string; focused: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(fact.content);
  const [needsVerification, setNeedsVerification] = useState(fact.needs_verification);
  const [verificationNote, setVerificationNote] = useState(fact.verification_note ?? "");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [candidateId, setCandidateId] = useState("");
  const [pendingState, setPendingState] = useState<"confirmed" | "withdrawn" | "restored" | null>(null);
  const [stateReason, setStateReason] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteReason, setDeleteReason] = useState("");

  useEffect(() => {
    setContent(fact.content);
    setNeedsVerification(fact.needs_verification);
    setVerificationNote(fact.verification_note ?? "");
  }, [fact.content, fact.needs_verification, fact.verification_note]);

  useEffect(() => {
    if (!focused) return;
    const element = document.getElementById(`fact-${fact.id}`);
    if (element && typeof element.scrollIntoView === "function") {
      element.scrollIntoView({ block: "center" });
    }
  }, [fact.id, focused]);

  const updateMutation = useMutation({
    mutationFn: () => api.patch(`/inspections/${inspectionId}/facts/${fact.id}/`, {
      content: content.trim(),
      needs_verification: needsVerification,
      verification_note: verificationNote.trim(),
      revision: fact.revision,
    }, { revision: fact.revision }),
    onSuccess: async () => {
      setEditing(false);
      await queryClient.invalidateQueries({ queryKey: ["facts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  const stateMutation = useMutation({
    mutationFn: () => api.post(`/inspections/${inspectionId}/facts/${fact.id}/${pendingState === "confirmed" ? "confirm" : pendingState === "withdrawn" ? "withdraw" : "restore"}/`, {
      reason: stateReason.trim() || undefined,
      expected_revision: fact.revision,
      revision: fact.revision,
    }, { revision: fact.revision, idempotencyKey: createIdempotencyKey(`${pendingState}-fact`) }),
    onSuccess: async () => {
      setPendingState(null);
      setStateReason("");
      await queryClient.invalidateQueries({ queryKey: ["facts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  const historyQuery = useQuery({
    queryKey: ["fact-history", fact.id],
    queryFn: () => api.get<HistoryEntry[] | PaginatedResponse<HistoryEntry>>(`/inspections/${inspectionId}/facts/${fact.id}/history/`),
    enabled: historyOpen,
  });

  const candidatesQuery = useQuery({
    queryKey: ["evidence-candidates", inspectionId],
    queryFn: () => api.get<EvidenceCandidate[] | PaginatedResponse<EvidenceCandidate>>(`/inspections/${inspectionId}/evidence-candidates/`),
    enabled: evidenceOpen,
  });
  const candidates = asList(candidatesQuery.data);

  const evidenceMutation = useMutation({
    mutationFn: () => {
      const candidate = candidates.find((item) => item.id === candidateId);
      if (!candidate) throw new Error("NO_EVIDENCE");
      return api.post(`/inspections/${inspectionId}/facts/${fact.id}/evidence/`, { ...candidate, revision: fact.revision }, {
        revision: fact.revision,
        idempotencyKey: createIdempotencyKey("link-evidence"),
      });
    },
    onSuccess: async () => {
      setEvidenceOpen(false);
      setCandidateId("");
      await queryClient.invalidateQueries({ queryKey: ["facts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/inspections/${inspectionId}/facts/${fact.id}/`, {
      confirm: true,
      reason: deleteReason.trim(),
      revision: fact.revision,
    }, {
      revision: fact.revision,
      idempotencyKey: createIdempotencyKey("delete-fact"),
    }),
    onSuccess: async () => {
      setDeleteOpen(false);
      setDeleteReason("");
      await queryClient.invalidateQueries({ queryKey: ["facts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["documents", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  const rawHistory = asList(historyQuery.data);
  const historyEntries = rawHistory.map((entry, index) => ({
    ...entry,
    actor_label: historyActor(entry),
    old_value: historyOldValue(entry) ?? (index > 0 ? historyNewValue(rawHistory[index - 1]) : {}),
    new_value: historyNewValue(entry),
  }));

  return (
    <article className={`fact-card panel ${focused ? "focused" : ""}`} id={`fact-${fact.id}`}>
      <header className="fact-card-header">
        <div className="fact-state-line">
          <StatusPill status={fact.review_state} />
          <span className="origin-badge">{fact.origin === "manual" ? "人工补充" : "AI 候选"}</span>
          {fact.needs_verification ? <span className="verification-badge">待核实</span> : null}
        </div>
        <div className="fact-card-actions">
          <button className="text-button" type="button" onClick={() => setEditing((value) => !value)}>{editing ? "取消编辑" : "编辑"}</button>
          <button className="text-button" type="button" onClick={() => setHistoryOpen((value) => !value)}>修改历史</button>
          <button className="text-button danger-text" type="button" onClick={() => { setDeleteReason(""); setDeleteOpen(true); }}>删除</button>
        </div>
      </header>

      {editing ? (
        <div className="fact-editor">
          <label className="field"><span>事实内容</span><textarea rows={4} value={content} onChange={(event) => setContent(event.target.value)} maxLength={5000} /></label>
          <label className="checkbox-field"><input type="checkbox" checked={needsVerification} onChange={(event) => setNeedsVerification(event.target.checked)} /><span>仍需人工核实</span></label>
          {needsVerification ? <label className="field"><span>待核实说明</span><input value={verificationNote} onChange={(event) => setVerificationNote(event.target.value)} maxLength={500} /></label> : null}
          {updateMutation.isError ? <span className="form-error">{getErrorMessage(updateMutation.error)}</span> : null}
          <div className="form-actions compact"><button className="button button-secondary button-small" type="button" onClick={() => setEditing(false)}>取消</button><button className="button button-primary button-small" type="button" disabled={!content.trim() || updateMutation.isPending} onClick={() => updateMutation.mutate()}>保存事实</button></div>
        </div>
      ) : (
        <div className="fact-content"><p data-assistant-source-id={fact.id} data-assistant-revision={fact.revision}>{fact.content}</p>{fact.verification_note ? <small>待核实：{fact.verification_note}</small> : null}</div>
      )}

      <div className="fact-evidence-section">
        <div className="fact-evidence-heading"><strong>关联证据</strong><button className="text-button" type="button" onClick={() => setEvidenceOpen((value) => !value)}>＋ 关联证据</button></div>
        {(fact.evidence ?? []).length > 0 ? <div className="evidence-list">{fact.evidence!.map((evidence, index) => <EvidenceLink key={evidence.id ?? `${evidence.type}-${index}`} evidence={evidence} inspectionId={inspectionId} />)}</div> : <p className="muted">尚未关联证据。重要事实应至少关联一个可回溯来源。</p>}
        {evidenceOpen ? (
          <div className="inline-editor">
            {candidatesQuery.isLoading ? <LoadingState label="正在加载可用证据" /> : candidates.length === 0 ? <p className="muted">没有可关联的材料片段、画面、照片或法规条款。</p> : <><label className="field"><span>选择证据</span><select value={candidateId} onChange={(event) => setCandidateId(event.target.value)}><option value="">请选择</option>{candidates.map((candidate) => <option value={candidate.id} key={candidate.id}>{candidate.label}</option>)}</select></label><button className="button button-primary button-small" type="button" disabled={!candidateId || evidenceMutation.isPending} onClick={() => evidenceMutation.mutate()}>确认关联</button></>}
            {evidenceMutation.isError ? <span className="form-error">{getErrorMessage(evidenceMutation.error)}</span> : null}
          </div>
        ) : null}
      </div>

      <footer className="fact-footer">
        <span>更新于 {formatDateTime(fact.updated_at ?? fact.created_at)}</span>
        <div>
          {fact.review_state !== "withdrawn" ? <button className="button button-ghost button-small" type="button" onClick={() => setPendingState("withdrawn")}>撤回</button> : <button className="button button-secondary button-small" type="button" onClick={() => setPendingState("restored")}>恢复为候选</button>}
          {fact.review_state === "candidate" ? <button className="button button-primary button-small" type="button" onClick={() => setPendingState("confirmed")}>人工确认事实</button> : null}
        </div>
      </footer>

      {historyOpen ? (
        <div className="object-history">
          <h4>事实修改历史</h4>
          {historyQuery.isLoading ? <LoadingState /> : historyEntries.length === 0 ? <p className="muted">暂无修改记录。</p> : historyEntries.map((entry) => <div className="history-mini-row" key={entry.id}><span>{formatDateTime(entry.created_at ?? entry.occurred_at)} · {entry.actor_label}</span><strong>{entry.action}</strong><details><summary>查看变更</summary><div className="history-diff"><pre>{compactJson(entry.old_value)}</pre><span>→</span><pre>{compactJson(entry.new_value)}</pre></div></details></div>)}
        </div>
      ) : null}

      <ConfirmDialog
        open={Boolean(pendingState)}
        title={pendingState === "confirmed" ? "确认这条检查事实？" : pendingState === "withdrawn" ? "撤回这条事实？" : "恢复为候选事实？"}
        description={<><p>{pendingState === "confirmed" ? "确认后可作为文书生成依据。AI 候选不会在没有此操作时自动变为已确认事实。" : pendingState === "withdrawn" ? "撤回会影响实际依赖该事实的已确认文书，并记录原因。" : "恢复后事实仍是候选状态，必须再次由用户明确确认，才能作为已确认事实使用。"}</p>{stateMutation.isError ? <span className="form-error">{getErrorMessage(stateMutation.error)}</span> : null}</>}
        tone={pendingState === "confirmed" ? "default" : "warning"}
        confirmLabel={pendingState === "confirmed" ? "确认事实" : pendingState === "withdrawn" ? "确认撤回" : "恢复为候选"}
        requireReason={pendingState === "withdrawn"}
        reason={stateReason}
        onReasonChange={setStateReason}
        busy={stateMutation.isPending}
        onCancel={() => setPendingState(null)}
        onConfirm={() => stateMutation.mutate()}
      />
      <ConfirmDialog
        open={deleteOpen}
        title="删除这条事实？"
        description={<><p>事实及其证据关联会从当前检查中删除；实际依赖该事实的已确认文书会退回初稿。删除操作会留下最小化审计记录。</p>{deleteMutation.isError ? <span className="form-error">{getErrorMessage(deleteMutation.error)}</span> : null}</>}
        tone="danger"
        confirmLabel="确认删除事实"
        requireReason
        reason={deleteReason}
        onReasonChange={setDeleteReason}
        busy={deleteMutation.isPending}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => deleteMutation.mutate()}
      />
    </article>
  );
}

export function FactsPanel({ inspectionId }: WorkspacePanelProps) {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [newFact, setNewFact] = useState("");
  const [needsVerification, setNeedsVerification] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [filter, setFilter] = useState<"all" | Fact["review_state"]>("all");

  const factsQuery = useQuery({
    queryKey: ["facts", inspectionId],
    queryFn: () => api.get<Fact[] | PaginatedResponse<Fact>>(`/inspections/${inspectionId}/facts/`),
  });
  const facts = asList(factsQuery.data);
  const filtered = filter === "all" ? facts : facts.filter((fact) => fact.review_state === filter);

  const createMutation = useMutation({
    mutationFn: () => api.post(`/inspections/${inspectionId}/facts/`, {
      content: newFact.trim(),
      needs_verification: needsVerification,
      origin: "manual",
    }, { idempotencyKey: createIdempotencyKey("manual-fact") }),
    onSuccess: async () => {
      setNewFact("");
      setNeedsVerification(false);
      setAddOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["facts", inspectionId] });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (newFact.trim()) createMutation.mutate();
  };

  if (factsQuery.isLoading) return <LoadingState label="正在加载事实与证据" />;
  if (factsQuery.isError) return <ErrorState message={getErrorMessage(factsQuery.error)} onRetry={() => void factsQuery.refetch()} />;

  return (
    <section aria-labelledby="facts-title">
      <div className="section-heading">
        <div><p className="eyebrow">人工审核闭环</p><h2 id="facts-title">事实与证据</h2><p>AI 结果默认是候选事实。没有可靠依据的信息只能标为待核实，不能伪装成结论。</p></div>
        <button className="button button-primary" type="button" onClick={() => setAddOpen((value) => !value)}>＋ 人工补充事实</button>
      </div>

      {addOpen ? (
        <form className="panel inline-create-panel" onSubmit={submit}>
          <label className="field"><span>事实内容</span><textarea rows={4} value={newFact} onChange={(event) => setNewFact(event.target.value)} maxLength={5000} placeholder="只记录现场材料可以支持的客观情况；不要在这里填写最终处罚结论。" autoFocus /></label>
          <label className="checkbox-field"><input type="checkbox" checked={needsVerification} onChange={(event) => setNeedsVerification(event.target.checked)} /><span>此内容仍需核实</span></label>
          {createMutation.isError ? <div className="notice notice-error">{getErrorMessage(createMutation.error)}</div> : null}
          <div className="form-actions compact"><button className="button button-ghost" type="button" onClick={() => setAddOpen(false)}>取消</button><button className="button button-primary" type="submit" disabled={!newFact.trim() || createMutation.isPending}>保存为人工事实</button></div>
        </form>
      ) : null}

      <div className="filter-bar" role="group" aria-label="筛选事实状态">
        {([['all', '全部'], ['candidate', '候选'], ['confirmed', '已确认'], ['withdrawn', '已撤回']] as const).map(([value, label]) => <button className={filter === value ? "active" : ""} type="button" key={value} onClick={() => setFilter(value)}>{label}<span>{value === "all" ? facts.length : facts.filter((fact) => fact.review_state === value).length}</span></button>)}
      </div>

      {filtered.length === 0 ? <EmptyState title={facts.length === 0 ? "还没有事实" : "没有符合筛选条件的事实"} description={facts.length === 0 ? "处理材料生成候选事实，或由检查员人工补充。" : "切换状态筛选查看其他事实。"} /> : <div className="fact-grid">{filtered.map((fact) => <FactCard key={fact.id} fact={fact} inspectionId={inspectionId} focused={searchParams.get("fact") === fact.id} />)}</div>}
    </section>
  );
}
