"""知识库管理与检索问答（PLAN 阶段 3）。"""
import shutil, uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.config import MEDIA_DIR
from ..core.models import KnowledgeDoc, KnowledgeChunk, User
from ..core.security import get_current_user, require_admin
from ..core.audit import audit
from ..domain import knowledge
from ..workers.worker import enqueue

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def _doc_out(d: KnowledgeDoc, db: Session) -> dict:
    n = db.query(KnowledgeChunk).filter_by(doc_id=d.id).count()
    return {"id": d.id, "title": d.title, "doc_type": d.doc_type, "filename": d.filename,
            "status": d.status, "error": d.error, "builtin": d.builtin, "chunks": n,
            "created_at": d.created_at.isoformat()}


@router.get("/types")
def types(user: User = Depends(get_current_user)):
    return knowledge.DOC_TYPES


@router.get("/docs")
def list_docs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_doc_out(d, db) for d in db.query(KnowledgeDoc).order_by(KnowledgeDoc.id.desc())]


@router.post("/docs")
async def upload_doc(title: str = Form(...), doc_type: str = Form("其他"), file: UploadFile = File(...),
                     db: Session = Depends(get_db), user: User = Depends(require_admin)):
    if doc_type not in knowledge.DOC_TYPES:
        raise HTTPException(400, f"文档类型须为：{knowledge.DOC_TYPES}")
    ext = Path(file.filename or "").suffix.lower()
    rel = Path("media") / "knowledge" / f"{uuid.uuid4().hex[:12]}{ext}"
    dest = MEDIA_DIR.parent / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    d = KnowledgeDoc(title=title, doc_type=doc_type, filename=file.filename or "",
                     file_path=str(rel), status="processing", created_by=user.id)
    db.add(d)
    db.flush()
    audit(db, user, "create", "knowledge_doc", d.id, note=f"上传知识文档：{title}（{doc_type}）")
    enqueue(db, inspection_id=0, kind="knowledge_ingest", payload={"doc_id": d.id})
    db.commit()
    return _doc_out(d, db)


@router.delete("/docs/{did}")
def delete_doc(did: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    d = db.get(KnowledgeDoc, did)
    if not d:
        raise HTTPException(404)
    if d.builtin:
        raise HTTPException(400, "预置法规文档不可删除")
    (MEDIA_DIR.parent / d.file_path).unlink(missing_ok=True)
    audit(db, user, "delete", "knowledge_doc", did, note=f"删除知识文档：{d.title}")
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.get("/docs/{did}/file")
def doc_file(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(KnowledgeDoc, did)
    if not d:
        raise HTTPException(404)
    return FileResponse(str(MEDIA_DIR.parent / d.file_path), filename=d.filename)


@router.get("/docs/{did}/chunks")
def doc_chunks(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(KnowledgeChunk).filter_by(doc_id=did).order_by(KnowledgeChunk.seq).all()
    return [{"id": r.id, "clause_label": r.clause_label, "text": r.text} for r in rows]


class QueryIn(BaseModel):
    question: str
    top_k: int = 6


@router.post("/query")
def query(body: QueryIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return knowledge.query(db, body.question, body.top_k)
