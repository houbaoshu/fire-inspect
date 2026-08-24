import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, ApiError, getErrorMessage } from "../../api/client";
import {
  asList,
  type Material,
  type PaginatedResponse,
  type Transcript,
  type TranscriptSegment,
} from "../../api/types";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatDuration } from "../../utils/format";
import type { WorkspacePanelProps } from "./workspace";

async function loadInspectionTranscripts(inspectionId: string): Promise<Transcript[]> {
  try {
    const response = await api.get<Transcript[] | PaginatedResponse<Transcript>>(`/inspections/${inspectionId}/transcripts/`);
    return asList(response);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) throw error;
  }

  const materialResponse = await api.get<Material[] | PaginatedResponse<Material>>(`/inspections/${inspectionId}/materials/`);
  const sourceMaterials = asList(materialResponse).filter((material) => material.kind === "video" || material.kind === "audio");
  const settled = await Promise.allSettled(
    sourceMaterials.map(async (material) => {
      const transcript = await api.get<Transcript>(`/materials/${material.id}/transcript/`);
      return { ...transcript, material_name: material.display_name };
    }),
  );
  const unexpected = settled.find(
    (result): result is PromiseRejectedResult => result.status === "rejected"
      && (!(result.reason instanceof ApiError) || result.reason.status !== 404),
  );
  if (unexpected) throw unexpected.reason;
  const transcripts: Transcript[] = [];
  for (const result of settled) {
    if (result.status === "fulfilled") transcripts.push(result.value);
  }
  return transcripts;
}

function SegmentEditor({ segment, transcriptId, transcriptRevision, materialId, inspectionId }: { segment: TranscriptSegment; transcriptId: string; transcriptRevision?: number; materialId: string; inspectionId: string }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(segment.text);
  const [role, setRole] = useState(segment.confirmed_role ?? "");
  const changed = text !== segment.text || role !== (segment.confirmed_role ?? "");

  useEffect(() => {
    setText(segment.text);
    setRole(segment.confirmed_role ?? "");
  }, [segment.confirmed_role, segment.text]);

  const mutation = useMutation({
    mutationFn: async () => {
      if (transcriptRevision === undefined) throw new Error("TRANSCRIPT_REVISION_MISSING");
      let nextRevision = transcriptRevision;
      if (text !== segment.text) {
        const response = await api.patch<{ transcript_revision?: number }>(`/transcripts/${transcriptId}/segments/${segment.id}/`, {
          text: text.trim(),
          transcript_revision: nextRevision,
        }, { revision: nextRevision });
        nextRevision = response.transcript_revision ?? nextRevision + 1;
      }
      if (role && role !== (segment.confirmed_role ?? "")) {
        await api.post(`/transcripts/${transcriptId}/segments/${segment.id}/confirm-role/`, {
          role,
          transcript_revision: nextRevision,
        }, { revision: nextRevision });
      }
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["transcripts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  return (
    <article className="transcript-segment">
      <div className="segment-time">
        <Link to={`/inspections/${inspectionId}/materials?material=${encodeURIComponent(materialId)}&t=${segment.start_ms}`} title="打开原材料时间">{formatDuration(segment.start_ms)}–{formatDuration(segment.end_ms)}</Link>
        <span>片段 {segment.sequence + 1}</span>
      </div>
      <div className="segment-editor">
        <textarea aria-label={`片段 ${segment.sequence + 1} 文字`} value={text} onChange={(event) => setText(event.target.value)} rows={2} maxLength={5000} />
        <div className="segment-controls">
          <span className="suggested-role">AI 建议：{segment.suggested_role === "question" ? "问" : segment.suggested_role === "answer" ? "答" : "未知"}</span>
          <label><span>人工确认角色</span><select aria-label={`片段 ${segment.sequence + 1} 角色`} value={role} onChange={(event) => setRole(event.target.value as "" | "question" | "answer")}><option value="" disabled={Boolean(segment.confirmed_role)}>未确认</option><option value="question">问</option><option value="answer">答</option></select></label>
          <button className="button button-secondary button-small" type="button" disabled={!changed || !text.trim() || transcriptRevision === undefined || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? "保存中" : "保存片段"}</button>
        </div>
        {mutation.isError ? <span className="form-error">{getErrorMessage(mutation.error)}</span> : null}
      </div>
    </article>
  );
}

export function TranscriptsPanel({ inspectionId }: WorkspacePanelProps) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fullText, setFullText] = useState("");

  const transcriptsQuery = useQuery({
    queryKey: ["transcripts", inspectionId],
    queryFn: () => loadInspectionTranscripts(inspectionId),
  });
  const transcripts = asList(transcriptsQuery.data);
  const selected = transcripts.find((transcript) => transcript.id === selectedId) ?? transcripts[0];

  useEffect(() => {
    if (selected) setFullText(selected.full_text);
  }, [selected?.full_text, selected?.id]);

  const saveFullText = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("NO_TRANSCRIPT");
      return api.patch(`/transcripts/${selected.id}/`, {
        full_text: fullText.trim(),
        revision: selected.revision,
      }, { revision: selected.revision });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["transcripts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  const submitFullText = (event: FormEvent) => {
    event.preventDefault();
    if (selected && fullText.trim() && fullText !== selected.full_text) saveFullText.mutate();
  };

  if (transcriptsQuery.isLoading) return <LoadingState label="正在加载转写" />;
  if (transcriptsQuery.isError) return <ErrorState message={getErrorMessage(transcriptsQuery.error)} onRetry={() => void transcriptsQuery.refetch()} />;

  return (
    <section aria-labelledby="transcripts-title">
      <div className="section-heading">
        <div><p className="eyebrow">人工可编辑</p><h2 id="transcripts-title">转写与问答角色</h2><p>完整连续文本与时间锚片段分别保存；片段边界不会直接污染文书正文。</p></div>
      </div>
      <div className="notice notice-info"><strong>角色说明：</strong>“问 / 答”只是语义角色，不代表声学说话人身份。询问笔录最终仅使用人工确认的角色与当前文字。</div>

      {transcripts.length === 0 ? (
        <EmptyState
          title="还没有转写结果"
          description="请先在材料页对视频或录音启动中文转写；自动转写失败或服务不可用时，也可在材料详情录入人工完整转写。"
          action={<Link className="button button-secondary" to={`/inspections/${inspectionId}/materials`}>前往材料页补充</Link>}
        />
      ) : (
        <div className="transcript-layout">
          <nav className="transcript-source-tabs" aria-label="选择材料转写">
            {transcripts.map((transcript) => <button className={selected?.id === transcript.id ? "active" : ""} type="button" key={transcript.id} onClick={() => setSelectedId(transcript.id)}><strong>{transcript.material_name || `材料 ${transcript.material_id.slice(0, 8)}`}</strong><span>{transcript.segments.length} 个时间片段</span></button>)}
          </nav>

          {selected ? (
            <div className="transcript-content">
              <form className="panel transcript-full-text" onSubmit={submitFullText}>
                <div className="subsection-heading"><div><h3>完整连续转写</h3><p>适用于事实整理和询问笔录候选生成。</p></div><span className="origin-badge">{selected.full_text_origin === "manual" ? "人工来源 / 人工可改" : selected.full_text ? "AI 初始 / 人工可改" : "待人工补充"}</span></div>
                <textarea aria-label="完整连续转写" rows={8} value={fullText} onChange={(event) => setFullText(event.target.value)} maxLength={200_000} />
                <div className="form-actions compact"><span>{fullText.length.toLocaleString("zh-CN")} 字</span><button className="button button-primary button-small" type="submit" disabled={!fullText.trim() || fullText === selected.full_text || saveFullText.isPending}>{saveFullText.isPending ? "保存中…" : "保存完整转写"}</button></div>
                {saveFullText.isError ? <div className="notice notice-error">{getErrorMessage(saveFullText.error)}</div> : null}
              </form>

              <div className="panel segments-panel">
                <div className="subsection-heading"><div><h3>时间锚片段</h3><p>点击时间可回到对应材料；证据始终保留材料 ID 与原始时间轴。</p></div></div>
                {selected.segments.length === 0 ? <EmptyState title="没有时间锚片段" description="当前仅有连续转写，可人工使用，但正式时间证据需要重新完成带时间戳的转写。" /> : selected.segments.map((segment) => <SegmentEditor key={segment.id} segment={segment} transcriptId={selected.id} transcriptRevision={selected.revision} materialId={selected.material_id} inspectionId={inspectionId} />)}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
