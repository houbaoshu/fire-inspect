import { useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, getErrorMessage } from "../api/client";
import type {
  AssistantNavigationAction,
  AssistantResponse,
  DocumentType,
} from "../api/types";

export interface AssistantQuery {
  question: string;
  inspection_id: string;
  route: string;
  selected_source_id?: string;
  selected_revision?: number;
  selected_text?: string;
}

export interface AssistantSelection {
  sourceId: string;
  revision: number;
  text: string;
}

interface AIAssistantProps {
  inspectionId?: string;
  route: string;
  selection?: AssistantSelection;
  query?: (request: AssistantQuery) => Promise<AssistantResponse>;
}

const DOCUMENT_SECTIONS: Record<DocumentType, string> = {
  record: "record",
  photo: "photo-document",
  interview: "interview",
};

function safeId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
}

function safeTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 604_800_000;
}

export function isSafeAssistantAction(
  action: AssistantNavigationAction,
  inspectionId?: string,
): boolean {
  if (!action || !inspectionId || action.inspection_id !== inspectionId || !safeId(action.inspection_id) || !safeId(action.target_id)) return false;
  switch (action.kind) {
    case "material_time":
      return safeId(action.material_id) && safeTimestamp(action.start_ms);
    case "photo":
      return action.photo_id === action.target_id && safeId(action.photo_id) && action.material_id == null && action.start_ms == null;
    case "fact":
      return action.fact_id === action.target_id && safeId(action.fact_id) && action.material_id == null && action.start_ms == null;
    case "clause":
      return action.clause_id === action.target_id && safeId(action.clause_id) && safeId(action.document_id) && action.material_id == null && action.start_ms == null;
    case "document":
      return action.document_id === action.target_id && safeId(action.document_id) && Object.hasOwn(DOCUMENT_SECTIONS, action.document_type) && action.material_id == null && action.start_ms == null;
    default:
      return false;
  }
}

function actionDestination(action: AssistantNavigationAction, inspectionId: string): { pathname: string; search: string } | null {
  const params = new URLSearchParams();
  switch (action.kind) {
    case "material_time":
      params.set("material", action.material_id);
      params.set("t", String(Math.round(action.start_ms)));
      return { pathname: `/inspections/${inspectionId}/materials`, search: `?${params}` };
    case "photo":
      params.set("photo", action.target_id);
      return { pathname: `/inspections/${inspectionId}/materials`, search: `?${params}` };
    case "fact":
      params.set("fact", action.target_id);
      return { pathname: `/inspections/${inspectionId}/facts`, search: `?${params}` };
    case "clause":
      params.set("document", action.document_id);
      params.set("clause", action.clause_id);
      return { pathname: `/inspections/${inspectionId}/regulations`, search: `?${params}` };
    case "document":
      params.set("document", action.document_id);
      return { pathname: `/inspections/${inspectionId}/${DOCUMENT_SECTIONS[action.document_type]}`, search: `?${params}` };
    default:
      return null;
  }
}

function actionLabel(action: AssistantNavigationAction): string {
  return ({
    material_time: "打开材料时间",
    photo: "打开照片",
    fact: "打开事实",
    clause: "打开法规条款",
    document: "打开文书",
  } as const)[action.kind] ?? "打开来源";
}

function citationLabel(sourceType: string): string {
  return ({ inspection: "检查信息", fact: "检查事实", transcript: "材料转写", document: "检查文书", photo: "现场照片", clause: "法规条款" } as Record<string, string>)[sourceType] ?? "授权来源";
}

export function AIAssistant({ inspectionId, route, selection, query }: AIAssistantProps) {
  const navigate = useNavigate();
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState<AssistantResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [navigationError, setNavigationError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const safeActions = useMemo(
    () => response?.read_only === true
      ? (response.navigation ?? []).filter((action) => isSafeAssistantAction(action, inspectionId))
      : [],
    [inspectionId, response],
  );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || loading || !inspectionId) return;
    setLoading(true);
    setError(null);
    setNavigationError(null);
    try {
      const request: AssistantQuery = {
        question: trimmed,
        inspection_id: inspectionId,
        route,
        ...(selection ? {
          selected_source_id: selection.sourceId,
          selected_revision: selection.revision,
          selected_text: selection.text.slice(0, 2000),
        } : {}),
      };
      const result = query
        ? await query(request)
        : await api.post<AssistantResponse>("/assistant/query", request);
      setResponse({
        text: typeof result.text === "string" ? result.text : "未获得可显示的回答。",
        citations: Array.isArray(result.citations) ? result.citations : [],
        navigation: Array.isArray(result.navigation) ? result.navigation : [],
        read_only: result.read_only === true,
      });
      setQuestion("");
    } catch (caught) {
      setError(getErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  };

  const navigateToAction = (action: AssistantNavigationAction) => {
    if (!inspectionId || !isSafeAssistantAction(action, inspectionId)) return;
    setNavigationError(null);
    const destination = actionDestination(action, inspectionId);
    if (destination) navigate(destination);
  };

  return (
    <aside className="assistant-panel" aria-label="只读 AI 助手">
      <header className="assistant-header">
        <div className="assistant-avatar" aria-hidden="true">AI</div>
        <div>
          <h2>检查助手</h2>
          <p>只读 · 回答与定位</p>
        </div>
        <span className="live-dot" title="只读模式" aria-label="只读模式" />
      </header>

      <div className="assistant-scope">
        <span>当前范围</span>
        <strong>{selection ? "本次检查 · 当前选中内容" : inspectionId ? "本次检查与当前页面" : "请先打开一次检查"}</strong>
      </div>

      <div className="assistant-thread" aria-live="polite">
        {!response && !error ? (
          <div className="assistant-welcome">
            <strong>我可以帮你快速回溯</strong>
            <p>询问事实依据、法规来源或材料位置。我不会修改、确认、删除或导出任何内容。</p>
          </div>
        ) : null}
        {response ? (
          <article className="assistant-answer">
            <span className="message-label">助手回答</span>
            <p>{response.text}</p>
            {response.citations.length > 0 ? (
              <div className="assistant-references">
                <h3>来源</h3>
                <ul>
                  {response.citations.map((citation, index) => (
                    <li key={`${citation.source_type}-${citation.source_id}-${index}`}>
                      <strong>{citationLabel(citation.source_type)}</strong>
                      <span>{citation.excerpt}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {safeActions.length > 0 ? (
              <div className="assistant-actions">
                {safeActions.map((action, index) => (
                  <button
                    className="assistant-action"
                    key={`${action.kind}-${action.target_id}-${index}`}
                    type="button"
                    onClick={() => navigateToAction(action)}
                  >
                    {actionLabel(action)}<span aria-hidden="true">→</span>
                  </button>
                ))}
              </div>
            ) : null}
          </article>
        ) : null}
        {error ? <div className="assistant-error" role="alert">{error}</div> : null}
        {navigationError ? <div className="assistant-error" role="alert">{navigationError}</div> : null}
      </div>

      <form className="assistant-composer" onSubmit={submit}>
        <label htmlFor="assistant-question" className="sr-only">向 AI 助手提问</label>
        <textarea
          id="assistant-question"
          aria-label="向 AI 助手提问"
          rows={3}
          maxLength={2000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={inspectionId ? "例如：这条事实的原始证据在哪里？" : "询问你有权访问的检查信息"}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className="assistant-composer-footer">
          <span>Enter 发送 · Shift+Enter 换行</span>
          <button className="button button-primary button-small" type="submit" disabled={loading || !question.trim() || !inspectionId}>
            {loading ? "查询中" : "发送"}
          </button>
        </div>
      </form>
    </aside>
  );
}
