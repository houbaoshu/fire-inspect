"""转写、关键帧、事实与证据（PLAN 阶段 1/2）。"""
import uuid, shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.config import MEDIA_DIR
from ..core.models import (Material, Transcript, TranscriptSegment, Keyframe, Fact, FactEvidence,
                           AuditLog, User, Document)
from ..core.security import get_current_user
from ..core.audit import audit

router = APIRouter(prefix="/api", tags=["evidence"])


# ---------- 转写 ----------

def _seg_out(s: TranscriptSegment) -> dict:
    return {"id": s.id, "seq": s.seq, "start_ms": s.start_ms, "end_ms": s.end_ms,
            "text": s.text, "speaker": s.speaker, "qa_role": s.qa_role, "qa_confirmed": s.qa_confirmed}


@router.get("/materials/{mid}/transcripts")
def get_transcripts(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = {}
    for kind in ("full", "window"):
        t = db.query(Transcript).filter_by(material_id=mid, kind=kind).first()
        out[kind] = {"id": t.id, "segments": [_seg_out(s) for s in
                     sorted(t.segments, key=lambda x: x.seq)]} if t else None
    return out


class SegmentPatch(BaseModel):
    text: str | None = None
    qa_role: str | None = None  # 问 / 答 / 空字符串清除
    qa_confirmed: bool | None = None


@router.patch("/segments/{sid}")
def patch_segment(sid: int, body: SegmentPatch, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    s = db.get(TranscriptSegment, sid)
    if not s:
        raise HTTPException(404)
    if body.text is not None and body.text != s.text:
        audit(db, user, "update", "segment", sid, field="text", old=s.text, new=body.text)
        s.text = body.text
    if body.qa_role is not None:
        audit(db, user, "update", "segment", sid, field="qa_role", old=s.qa_role or "", new=body.qa_role)
        s.qa_role = body.qa_role or None
    if body.qa_confirmed is not None:
        s.qa_confirmed = body.qa_confirmed
    db.commit()
    return _seg_out(s)


# ---------- 关键帧 ----------

def _kf_out(k: Keyframe) -> dict:
    return {"id": k.id, "material_id": k.material_id, "ts_ms": k.ts_ms, "caption": k.caption,
            "quality_score": k.quality_score, "source": k.source,
            "url": f"/api/keyframes/{k.id}/image"}


@router.get("/materials/{mid}/keyframes")
def list_keyframes(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Keyframe).filter_by(material_id=mid, deleted=False).order_by(Keyframe.ts_ms).all()
    return [_kf_out(k) for k in rows]


@router.get("/inspections/{iid}/keyframes")
def inspection_keyframes(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Keyframe).filter_by(inspection_id=iid, deleted=False).order_by(Keyframe.id).all()
    return [_kf_out(k) for k in rows]


@router.get("/keyframes/{kid}/image")
def keyframe_image(kid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = db.get(Keyframe, kid)
    if not k:
        raise HTTPException(404)
    return FileResponse(str(MEDIA_DIR.parent / k.image_path))


@router.delete("/keyframes/{kid}")
def delete_keyframe(kid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = db.get(Keyframe, kid)
    if not k:
        raise HTTPException(404)
    k.deleted = True  # 软删除错误候选；文件保留
    db.commit()
    return {"ok": True}


@router.post("/inspections/{iid}/keyframes")
async def upload_keyframe(iid: int, file: UploadFile = File(...),
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ext = Path(file.filename or "x.jpg").suffix.lower()
    rel = Path("media") / f"i{iid}" / f"photo_{uuid.uuid4().hex[:10]}{ext}"
    dest = MEDIA_DIR.parent / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    k = Keyframe(inspection_id=iid, material_id=None, ts_ms=None, image_path=str(rel), source="uploaded")
    db.add(k)
    db.commit()
    return _kf_out(k)


# ---------- 事实与证据 ----------

def _fact_out(f: Fact) -> dict:
    return {"id": f.id, "content": f.content, "status": f.status, "source": f.source,
            "updated_at": f.updated_at.isoformat() if f.updated_at else None,
            "evidences": [{"id": e.id, "evidence_type": e.evidence_type, "material_id": e.material_id,
                           "ref": e.ref, "note": e.note} for e in f.evidences]}


@router.get("/inspections/{iid}/facts")
def list_facts(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_fact_out(f) for f in db.query(Fact).filter_by(inspection_id=iid).order_by(Fact.id)]


class FactIn(BaseModel):
    content: str


@router.post("/inspections/{iid}/facts")
def create_fact(iid: int, body: FactIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    f = Fact(inspection_id=iid, content=body.content, status="confirmed", source="user", created_by=user.id)
    db.add(f)
    db.flush()
    audit(db, user, "create", "fact", f.id, note="人工补充事实")
    db.commit()
    return _fact_out(f)


class FactPatch(BaseModel):
    content: str | None = None
    status: str | None = None  # confirmed / candidate


@router.patch("/facts/{fid}")
def patch_fact(fid: int, body: FactPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    f = db.get(Fact, fid)
    if not f:
        raise HTTPException(404)
    if body.content is not None and body.content != f.content:
        audit(db, user, "update", "fact", fid, field="content", old=f.content, new=body.content)
        f.content = body.content
    if body.status is not None and body.status != f.status:
        audit(db, user, "update", "fact", fid, field="status", old=f.status, new=body.status)
        f.status = body.status
    db.commit()
    _revert_confirmed_docs(db, f.inspection_id, user, reason=f"事实 #{fid} 变更")
    db.commit()
    return _fact_out(f)


@router.delete("/facts/{fid}")
def delete_fact(fid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    f = db.get(Fact, fid)
    if not f:
        raise HTTPException(404)
    audit(db, user, "delete", "fact", fid, note=f"删除事实：{f.content[:50]}")
    iid = f.inspection_id
    db.delete(f)
    _revert_confirmed_docs(db, iid, user, reason=f"事实 #{fid} 删除")
    db.commit()
    return {"ok": True}


def _revert_confirmed_docs(db: Session, inspection_id: int, user: User, reason: str):
    """事实/共享字段变化 → 已确认文书退回初稿（PRD §16）。"""
    for d in db.query(Document).filter_by(inspection_id=inspection_id, status="confirmed"):
        d.status = "draft"
        audit(db, user, "status_change", "document", d.id,
              field="status", old="confirmed", new="draft", note=f"{reason}，退回初稿待重新确认")


class EvidenceIn(BaseModel):
    evidence_type: str  # video_segment / keyframe / transcript_segment / knowledge_chunk
    material_id: int | None = None
    ref: dict = {}
    note: str = ""


@router.post("/facts/{fid}/evidences")
def add_evidence(fid: int, body: EvidenceIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    f = db.get(Fact, fid)
    if not f:
        raise HTTPException(404)
    e = FactEvidence(fact_id=fid, **body.model_dump())
    db.add(e)
    db.flush()
    audit(db, user, "create", "fact_evidence", e.id, note=f"事实 #{fid} 关联 {body.evidence_type}")
    db.commit()
    return {"id": e.id}


@router.delete("/evidences/{eid}")
def del_evidence(eid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    e = db.get(FactEvidence, eid)
    if not e:
        raise HTTPException(404)
    db.delete(e)
    db.commit()
    return {"ok": True}


@router.get("/facts/{fid}/history")
def fact_history(fid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(AuditLog).filter_by(object_type="fact", object_id=fid)
            .order_by(AuditLog.at.desc()).all())
    return [{"id": r.id, "at": r.at.isoformat(), "actor": r.actor_id, "action": r.action,
             "detail": r.detail} for r in rows]
