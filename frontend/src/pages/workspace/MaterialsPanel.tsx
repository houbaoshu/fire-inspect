import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { api, createIdempotencyKey, getErrorMessage } from "../../api/client";
import { asList, type Material, type PaginatedResponse, type Transcript } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusPill } from "../../components/StatusPill";
import { formatBytes, formatDateTime, formatDuration } from "../../utils/format";
import { materialSteps, STEP_LABELS, type WorkspacePanelProps } from "./workspace";

const ALLOWED_EXTENSIONS = [".mp4", ".jpg", ".jpeg", ".png", ".mp3", ".wav"];

function localAssetUrl(candidate: string | undefined, fallback: string): string {
  if (!candidate) return fallback;
  try {
    const url = new URL(candidate, window.location.origin);
    return url.origin === window.location.origin ? `${url.pathname}${url.search}` : fallback;
  } catch {
    return fallback;
  }
}

function materialKindLabel(kind: string): string {
  return ({ video: "视频", image: "照片", audio: "录音", text: "文字说明" }[kind] ?? "材料");
}

function stepNamesFor(material: Material): string[] {
  if (material.kind === "video") return ["transcode", "audio_extract", "asr", "keyframes", "vision", "fact_extraction"];
  if (material.kind === "audio") return ["asr", "fact_extraction"];
  if (material.kind === "image") return ["vision"];
  if (material.kind === "text") return ["fact_extraction"];
  return [];
}

export function MaterialsPanel({ inspectionId }: WorkspacePanelProps) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [textMaterial, setTextMaterial] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Material | null>(null);
  const [deleteReason, setDeleteReason] = useState("");
  const [manualTranscriptDrafts, setManualTranscriptDrafts] = useState<Record<string, string>>({});
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  const materialsQuery = useQuery({
    queryKey: ["materials", inspectionId],
    queryFn: () => api.get<Material[] | PaginatedResponse<Material>>(`/inspections/${inspectionId}/materials/`),
    refetchInterval: (query) => {
      const items = asList(query.state.data as Material[] | PaginatedResponse<Material> | undefined);
      return items.some((item) => materialSteps(item).some((step) => step.status === "queued" || step.status === "running")) ? 3000 : false;
    },
  });

  const materials = asList(materialsQuery.data);
  const hasMediaMaterials = materials.some((material) => material.kind === "video" || material.kind === "audio");
  const transcriptsQuery = useQuery({
    queryKey: ["transcripts", inspectionId],
    queryFn: () => api.get<Transcript[] | PaginatedResponse<Transcript>>(`/inspections/${inspectionId}/transcripts/`),
    enabled: hasMediaMaterials,
  });
  const selectedId = searchParams.get("material") ?? searchParams.get("photo");
  const selected = useMemo(
    () => selectedId ? materials.find((material) => material.id === selectedId) : materials[0],
    [materials, selectedId],
  );
  const selectedTargetMissing = Boolean(selectedId && !selected);
  const selectedTranscript = selected
    ? asList(transcriptsQuery.data).find((transcript) => transcript.material_id === selected.id)
    : undefined;
  const manualTranscriptText = selected ? manualTranscriptDrafts[selected.id] ?? "" : "";

  useEffect(() => {
    const target = Number(searchParams.get("t"));
    const mediaElement = selected?.kind === "audio" ? audioRef.current : videoRef.current;
    if (!mediaElement || !Number.isFinite(target) || target < 0) return;
    const seek = () => {
      mediaElement.currentTime = target / 1000;
    };
    if (mediaElement.readyState >= 1) seek();
    else mediaElement.addEventListener("loadedmetadata", seek, { once: true });
    return () => mediaElement.removeEventListener("loadedmetadata", seek);
  }, [searchParams, selected?.id]);

  const uploadMutation = useMutation({
    mutationFn: async () => {
      if (selectedFiles.length === 0 && !textMaterial.trim()) throw new Error("NO_MATERIAL");
      if (selectedFiles.length > 0) {
        for (const file of selectedFiles) {
          const formData = new FormData();
          formData.append("file", file, file.name);
          await api.post(`/inspections/${inspectionId}/materials/`, formData, {
            idempotencyKey: createIdempotencyKey("upload-material"),
          });
        }
      }
      if (textMaterial.trim()) {
        await api.post(`/inspections/${inspectionId}/materials/`, {
          kind: "text",
        display_name: `文字说明 ${formatDateTime(new Date().toISOString())}`,
          text: textMaterial.trim(),
        }, { idempotencyKey: createIdempotencyKey("create-text-material") });
      }
    },
    onSuccess: async () => {
      setSelectedFiles([]);
      setTextMaterial("");
      setUploadOpen(false);
      setUploadError(null);
      await queryClient.invalidateQueries({ queryKey: ["materials", inspectionId] });
    },
  });

  const processMutation = useMutation({
    mutationFn: ({ material, step }: { material: Material; step: string }) =>
      api.post(`/materials/${material.id}/jobs/${step}/`, {}, {
        revision: material.revision,
        idempotencyKey: createIdempotencyKey(`${step}-${material.id}`),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["materials", inspectionId] });
    },
  });

  const retryMutation = useMutation({
    mutationFn: (jobId: string) => api.post(`/jobs/${jobId}/retry/`, {}, { idempotencyKey: createIdempotencyKey(`retry-${jobId}`) }),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["materials", inspectionId] }),
  });

  const manualTranscriptMutation = useMutation({
    mutationFn: ({ materialId, fullText }: { materialId: string; fullText: string }) =>
      api.put<Transcript>(`/materials/${materialId}/transcript`, {
        full_text: fullText.trim(),
      }, { idempotencyKey: createIdempotencyKey(`manual-transcript-${materialId}`) }),
    onSuccess: async (_transcript, variables) => {
      setManualTranscriptDrafts((current) => ({ ...current, [variables.materialId]: "" }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["transcripts", inspectionId] }),
        queryClient.invalidateQueries({ queryKey: ["materials", inspectionId] }),
      ]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!deleteTarget) return Promise.resolve();
      return api.delete(`/materials/${deleteTarget.id}/`, { confirm: true, reason: deleteReason.trim() }, {
        revision: deleteTarget.revision,
        idempotencyKey: createIdempotencyKey("delete-material"),
      });
    },
    onSuccess: async () => {
      setDeleteTarget(null);
      setDeleteReason("");
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete("material");
      nextParams.delete("photo");
      nextParams.delete("t");
      setSearchParams(nextParams, { replace: true });
      await queryClient.invalidateQueries({ queryKey: ["materials", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["facts", inspectionId] });
      await queryClient.invalidateQueries({ queryKey: ["document"] });
    },
  });

  const chooseFiles = (files: FileList | null) => {
    const chosen = Array.from(files ?? []);
    const unsupported = chosen.find((file) => !ALLOWED_EXTENSIONS.some((extension) => file.name.toLocaleLowerCase().endsWith(extension)));
    if (unsupported) {
      setUploadError(`“${unsupported.name}”不是支持的 MP4、JPEG、PNG、MP3 或 WAV 文件。`);
      setSelectedFiles([]);
      return;
    }
    setUploadError(null);
    setSelectedFiles(chosen);
  };

  const submitUpload = (event: FormEvent) => {
    event.preventDefault();
    uploadMutation.mutate();
  };

  if (materialsQuery.isLoading) return <LoadingState label="正在加载检查材料" />;
  if (materialsQuery.isError) return <ErrorState message={getErrorMessage(materialsQuery.error)} onRetry={() => void materialsQuery.refetch()} />;

  return (
    <section aria-labelledby="materials-title">
      <div className="section-heading">
        <div><p className="eyebrow">原始输入</p><h2 id="materials-title">检查材料</h2><p>原件始终保留；转写、关键画面、视觉和事实提取可独立运行与重试。</p></div>
        <button className="button button-primary" type="button" onClick={() => setUploadOpen((value) => !value)}>＋ 添加材料</button>
      </div>

      {uploadOpen ? (
        <form className="panel upload-panel" onSubmit={submitUpload}>
          <div className="upload-grid">
            <label className="file-drop-zone">
              <input type="file" multiple accept=".mp4,.jpg,.jpeg,.png,.mp3,.wav,video/mp4,image/jpeg,image/png,audio/mpeg,audio/wav" onChange={(event) => chooseFiles(event.target.files)} />
              <strong>选择视频、照片或录音</strong>
              <span>支持 MP4、JPEG、PNG、MP3、WAV；服务端还会校验真实格式、损坏状态和大小限制。</span>
              {selectedFiles.length > 0 ? <small>已选择 {selectedFiles.length} 个文件：{selectedFiles.map((file) => file.name).join("、")}</small> : null}
            </label>
            <div className="upload-or" aria-hidden="true">或</div>
            <label className="field text-material-field"><span>添加文字说明</span><textarea rows={5} value={textMaterial} onChange={(event) => setTextMaterial(event.target.value)} maxLength={20_000} placeholder="记录无法通过媒体表达的现场情况；请勿把待核实内容写成已确认结论。" /></label>
          </div>
          {uploadError ? <div className="notice notice-error" role="alert">{uploadError}</div> : null}
          {uploadMutation.isError ? <div className="notice notice-error" role="alert">{getErrorMessage(uploadMutation.error)}</div> : null}
          <div className="form-actions"><button className="button button-ghost" type="button" onClick={() => setUploadOpen(false)}>收起</button><button className="button button-primary" disabled={uploadMutation.isPending || (selectedFiles.length === 0 && !textMaterial.trim())} type="submit">{uploadMutation.isPending ? "正在上传…" : "提交材料"}</button></div>
        </form>
      ) : null}

      {materials.length === 0 ? (
        <EmptyState title="尚未添加材料" description="上传视频、照片或录音，也可以先添加文字说明。原始材料不会因 AI 步骤失败而丢失。" action={<button className="button button-primary" type="button" onClick={() => setUploadOpen(true)}>添加第一份材料</button>} />
      ) : (
        <div className="materials-layout">
          <div className="material-list panel" aria-label="材料列表">
            {materials.map((material) => {
              const active = selected?.id === material.id;
              const steps = materialSteps(material);
              const failure = steps.some((step) => step.status === "failed");
              const processing = steps.some((step) => step.status === "running" || step.status === "queued");
              return (
                <button
                  className={active ? "material-list-item active" : "material-list-item"}
                  key={material.id}
                  type="button"
                  onClick={() => setSearchParams({ material: material.id }, { replace: true })}
                >
                  <span className={`material-kind kind-${material.kind}`} aria-hidden="true">{material.kind === "video" ? "▶" : material.kind === "image" ? "▧" : material.kind === "audio" ? "∿" : "文"}</span>
                  <span className="material-list-copy"><strong>{material.display_name}</strong><small>{materialKindLabel(material.kind)} · {formatBytes(material.byte_size)}{material.duration_ms ? ` · ${formatDuration(material.duration_ms)}` : ""}</small></span>
                  {failure ? <StatusPill status="failed" /> : processing ? <StatusPill status="running" /> : null}
                </button>
              );
            })}
          </div>

          {selectedTargetMissing ? (
            <div className="material-detail panel">
              <ErrorState
                message="指定材料不存在或你无权访问。为避免跳到错误文件，系统没有回退播放其他材料。"
                onRetry={() => {
                  const nextParams = new URLSearchParams(searchParams);
                  nextParams.delete("material");
                  nextParams.delete("photo");
                  nextParams.delete("t");
                  setSearchParams(nextParams, { replace: true });
                }}
              />
            </div>
          ) : selected ? (
            <article className="material-detail panel">
              <header className="material-detail-header">
                <div><p className="eyebrow">{materialKindLabel(selected.kind)}</p><h3>{selected.display_name}</h3><p>{formatBytes(selected.byte_size)} · {selected.duration_ms ? formatDuration(selected.duration_ms) : "无时长"} · 添加于 {formatDateTime(selected.created_at)}</p></div>
                <button className="button button-danger-outline button-small" type="button" onClick={() => { setDeleteTarget(selected); setDeleteReason(""); }}>删除材料</button>
              </header>

              <div className="material-preview">
                {selected.kind === "video" ? (
                  <video ref={videoRef} controls preload="metadata" src={localAssetUrl(selected.playback_url, `${api.baseUrl}/materials/${selected.id}/play/`)}>浏览器不支持视频播放。</video>
                ) : null}
                {selected.kind === "audio" ? <audio ref={audioRef} controls preload="metadata" src={localAssetUrl(selected.playback_url, `${api.baseUrl}/materials/${selected.id}/play/`)}>浏览器不支持音频播放。</audio> : null}
                {selected.kind === "image" ? <img src={localAssetUrl(selected.thumbnail_url, `${api.baseUrl}/materials/${selected.id}/download/`)} alt={selected.display_name} /> : null}
                {selected.kind === "text" ? <div className="text-material-preview">文字说明已安全保存。请在事实页确认其中可作为检查事实的内容。</div> : null}
              </div>

              <div className="processing-section">
                <div className="subsection-heading"><div><h4>独立处理步骤</h4><p>某一步失败不会覆盖其他成功结果。</p></div></div>
                <div className="step-list">
                  {stepNamesFor(selected).map((stepName) => {
                    const existing = materialSteps(selected).find((step) => step.name === stepName);
                    const status = existing?.status ?? "not_started";
                    return (
                      <div className="step-row" key={stepName}>
                        <div><strong>{STEP_LABELS[stepName] ?? stepName}</strong>{existing?.safe_error_message ? <small className="step-error">{existing.safe_error_message}</small> : null}</div>
                        <StatusPill status={status} />
                        {status === "failed" && existing?.job_id ? (
                          <button className="button button-secondary button-small" type="button" disabled={retryMutation.isPending} onClick={() => retryMutation.mutate(existing.job_id!)}>仅重试此步骤</button>
                        ) : status === "not_started" || status === "superseded" ? (
                          <button className="button button-secondary button-small" type="button" disabled={processMutation.isPending} onClick={() => processMutation.mutate({ material: selected, step: stepName })}>开始处理</button>
                        ) : <span className="step-action-placeholder" />}
                      </div>
                    );
                  })}
                </div>
                {processMutation.isError || retryMutation.isError ? <div className="notice notice-error">{getErrorMessage(processMutation.error ?? retryMutation.error)}</div> : null}
              </div>

              {selected.kind === "video" || selected.kind === "audio" ? (
                <section className="manual-transcript-panel" aria-labelledby={`manual-transcript-${selected.id}`}>
                  {transcriptsQuery.isLoading ? <LoadingState label="正在确认转写状态" /> : transcriptsQuery.isError ? (
                    <ErrorState message={getErrorMessage(transcriptsQuery.error)} onRetry={() => void transcriptsQuery.refetch()} />
                  ) : selectedTranscript ? (
                    <div className="notice notice-success manual-transcript-existing">
                      <span className="origin-badge">{selectedTranscript.full_text_origin === "manual" ? "人工来源" : "AI 来源"}</span>
                      <span>该材料已有{selectedTranscript.full_text_origin === "manual" ? "人工" : ""}转写，请在转写页继续核对或编辑。</span>
                      <Link to={`/inspections/${inspectionId}/transcripts`}>前往转写页</Link>
                    </div>
                  ) : (
                    <form onSubmit={(event) => {
                      event.preventDefault();
                      if (manualTranscriptText.trim()) {
                        manualTranscriptMutation.mutate({ materialId: selected.id, fullText: manualTranscriptText });
                      }
                    }}>
                      <div className="subsection-heading">
                        <div><h4 id={`manual-transcript-${selected.id}`}>人工补充完整转写</h4><p>{materialSteps(selected).some((step) => step.name === "asr" && step.status === "failed") ? "自动转写失败时，原始材料仍会保留；你可以在这里录入连续全文。" : "尚无转写时，可以录入完整连续文本作为人工降级路径。"}</p></div>
                        <span className="origin-badge">将保存为人工来源</span>
                      </div>
                      <label className="field">
                        <span>完整连续转写</span>
                        <textarea
                          aria-label={`${selected.display_name} 人工连续转写`}
                          rows={7}
                          maxLength={200_000}
                          placeholder="输入从材料开始到结束的连续全文；不确定内容请明确标注待核实。"
                          value={manualTranscriptText}
                          onChange={(event) => setManualTranscriptDrafts((current) => ({ ...current, [selected.id]: event.target.value }))}
                        />
                      </label>
                      {!selected.duration_ms ? <div className="notice notice-warning">材料时长尚未确认，请先完成媒体处理后再保存人工转写。</div> : null}
                      {manualTranscriptMutation.isError ? <div className="notice notice-error" role="alert">{getErrorMessage(manualTranscriptMutation.error)}</div> : null}
                      <div className="form-actions compact">
                        <span>{manualTranscriptText.length.toLocaleString("zh-CN")} 字 · 人工来源</span>
                        <button className="button button-primary button-small" type="submit" disabled={!manualTranscriptText.trim() || !selected.duration_ms || manualTranscriptMutation.isPending}>{manualTranscriptMutation.isPending ? "正在保存…" : "保存人工转写"}</button>
                      </div>
                    </form>
                  )}
                </section>
              ) : null}
            </article>
          ) : null}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除这份材料？"
        description={<><p>材料的转写、关键画面和播放副本将同步删除。已经人工确认或创建的事实不会被静默删除，但会移除缺失证据、标记待核实，并使实际依赖的文书退回初稿。</p>{deleteMutation.isError ? <span className="form-error">{getErrorMessage(deleteMutation.error)}</span> : null}</>}
        tone="danger"
        confirmLabel="确认删除材料"
        requireReason
        reason={deleteReason}
        onReasonChange={setDeleteReason}
        busy={deleteMutation.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteMutation.mutate()}
      />
    </section>
  );
}
