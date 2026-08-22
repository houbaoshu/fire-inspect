"""全局右侧 AI 助手（PLAN 阶段 6）：上下文感知问答 + 导航动作。

- 上下文注入：当前检查/事实/证据/转写/文书/关键帧（按当前用户可见范围，PRD §14）；
- 动作：助手返回结构化导航意图，由前端执行跳转；助手不做任何写操作（PRD §13）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.config import settings
from ..core.models import (Inspection, Material, Fact, FactEvidence, Transcript, TranscriptSegment,
                           Keyframe, Document, KnowledgeChunk, User)
from ..core.security import get_current_user
from ..core import ai
from ..domain import knowledge

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

SYSTEM = """你是消防监督检查辅助系统的全局 AI 助手，嵌入在业务页面右侧。
你可以访问用户当前检查的上下文（基础信息、已确认/候选事实、转写、关键画面、文书、法规检索）。
要求：
1. 回答简明、面向执法实务；引用上下文中的具体内容。
2. 禁止编造上下文中不存在的事实、法规或证据；没有就直说。
3. 如用户的问题适合"跳转定位"，在 actions 中给出导航动作，可选类型：
   - {"type": "open_video", "material_id": N, "ts_ms": M} 跳视频时间
   - {"type": "open_keyframe", "keyframe_id": N} 打开照片
   - {"type": "open_fact", "fact_id": N} 定位检查事实
   - {"type": "open_document", "document_id": N} 打开文书
   - {"type": "open_knowledge", "chunk_id": N} 打开法规原文
4. 可适当给出一条简短操作建议（suggestion 字段），但不得替用户执行修改。
输出 JSON：{"answer": "...", "actions": [...], "suggestion": "..."}"""


def _build_context(db: Session, inspection_id: int | None) -> str:
    if not inspection_id:
        return "（用户当前不在具体检查页面）"
    insp = db.get(Inspection, inspection_id)
    if not insp:
        return "（检查不存在）"
    parts = [f"当前检查：单位={insp.unit_name}，地址={insp.address}，"
             f"时间={insp.inspected_at}，检查人员={insp.checker_names}"]
    facts = db.query(Fact).filter_by(inspection_id=inspection_id).all()
    if facts:
        parts.append("检查事实：\n" + "\n".join(
            f"  [#{f.id}]（{'已确认' if f.status == 'confirmed' else '候选'}）{f.content}"
            + (f" 证据:{[e.evidence_type + ':' + str(e.ref) for e in f.evidences]}" if f.evidences else " 无关联证据")
            for f in facts))
    mats = db.query(Material).filter_by(inspection_id=inspection_id).all()
    for m in mats:
        t = db.query(Transcript).filter_by(material_id=m.id, kind="full").first()
        if t and t.segments:
            parts.append(f"材料 #{m.id}《{m.filename}》转写：\n{t.segments[0].text[:1500]}")
        segs = (db.query(TranscriptSegment).join(Transcript)
                .filter(Transcript.material_id == m.id, Transcript.kind == "window")
                .order_by(TranscriptSegment.seq).all())
        if segs:
            parts.append(f"材料 #{m.id} 分段时间锚：\n" + "\n".join(
                f"  [{s.start_ms//1000}-{s.end_ms//1000}s] {s.text[:80]}" for s in segs[:20]))
    kfs = db.query(Keyframe).filter_by(inspection_id=inspection_id, deleted=False).all()
    if kfs:
        parts.append("关键画面：\n" + "\n".join(
            f"  [帧#{k.id}] 材料#{k.material_id} {k.ts_ms and k.ts_ms//1000}s {k.caption[:60]}" for k in kfs[:30]))
    docs = db.query(Document).filter_by(inspection_id=inspection_id).all()
    if docs:
        parts.append("文书：\n" + "\n".join(f"  [#{d.id}] {d.name}（{d.status}）" for d in docs))
    return "\n\n".join(parts)


class ChatIn(BaseModel):
    message: str
    inspection_id: int | None = None
    page: str = ""          # 当前页面标识（前端路由）
    selected: dict = {}     # 用户当前选中内容（事实/字段等）


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not settings.ai_available("llm"):
        raise HTTPException(503, "AI 助手不可用：LLM 端点未配置（降级模式，业务功能不受影响）")
    ctx = _build_context(db, body.inspection_id)
    user_msg = f"【当前页面】{body.page}\n"
    if body.selected:
        user_msg += f"【用户选中】{body.selected}\n"
    user_msg += f"【检查上下文】\n{ctx}\n\n【用户问题】{body.message}"
    # 法规类问题：先检索知识库注入
    try:
        hits = knowledge.retrieve(db, body.message, top_k=3)
        if hits:
            user_msg += "\n\n【知识库检索结果】\n" + "\n".join(
                f"[chunk#{h['chunk_id']}] 《{h['doc_title']}》{h['clause_label']}：{h['text'][:400]}"
                for h in hits)
    except ai.AIError:
        pass
    r = ai.llm_json(SYSTEM, user_msg)
    return {"answer": r.get("answer", ""), "actions": r.get("actions") or [],
            "suggestion": r.get("suggestion", "")}
