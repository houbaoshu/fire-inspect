"""文书创建、生成、编辑、确认、渲染导出（PLAN 阶段 5）。"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.config import MEDIA_DIR, EXPORT_DIR
from ..core.models import Document, DocumentField, Inspection, Template, AuditLog, User
from ..core.security import get_current_user
from ..core.audit import audit
from ..domain import doc_generate

router = APIRouter(prefix="/api", tags=["documents"])


def _doc_out(d: Document) -> dict:
    return {"id": d.id, "inspection_id": d.inspection_id, "template_id": d.template_id,
            "doc_type": d.doc_type, "doc_type_name": doc_generate.DOC_TYPE_NAME.get(d.doc_type, d.doc_type),
            "name": d.name, "status": d.status, "has_file": bool(d.file_path),
            "created_at": d.created_at.isoformat(),
            "confirmed_at": d.confirmed_at.isoformat() if d.confirmed_at else None,
            "fields": [{"id": f.id, "field_label": f.field_label, "value": f.value, "origin": f.origin,
                        "per_frame": f.per_frame, "frame_key": f.frame_key,
                        "updated_at": f.updated_at.isoformat() if f.updated_at else None}
                       for f in d.fields if f.field_label != "__frame__"]}


@router.get("/inspections/{iid}/documents")
def list_documents(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_doc_out(d) for d in db.query(Document).filter_by(inspection_id=iid).order_by(Document.id)]


class DocCreate(BaseModel):
    doc_type: str  # record / photo / interrogation
    template_id: int
    name: str = ""
    frame_ids: list[int] = []  # 照片文书：已选关键帧


@router.post("/inspections/{iid}/documents")
def create_document(iid: int, body: DocCreate, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    insp = db.get(Inspection, iid)
    tpl = db.get(Template, body.template_id)
    if not insp or not tpl:
        raise HTTPException(404, "检查或模板不存在")
    if tpl.doc_type != body.doc_type:
        raise HTTPException(400, "模板类型与文书类型不一致")
    name = body.name or doc_generate.default_name(insp, body.doc_type)
    d = Document(inspection_id=iid, template_id=tpl.id, doc_type=body.doc_type, name=name,
                 created_by=user.id)
    db.add(d)
    db.flush()
    doc_generate.generate_values(db, d, frame_ids=[str(x) for x in body.frame_ids] or None)
    try:
        doc_generate.render_document(db, d)
    except Exception as e:
        audit(db, user, "create", "document", d.id, note=f"创建文书：{name}（首次渲染失败：{e}）")
    else:
        audit(db, user, "create", "document", d.id, note=f"创建文书：{name}")
    db.commit()
    return _doc_out(d)


@router.get("/documents/{did}")
def get_document(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    return _doc_out(d)


class FieldValuePatch(BaseModel):
    value: str


@router.patch("/documents/{did}/fields")
def patch_field_value(did: int, body: dict, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """人工编辑字段：origin 置 user，此后 AI 重新生成不无提示覆盖（PRD §10）。"""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    label, frame_key, value = body["field_label"], body.get("frame_key", ""), body.get("value", "")
    f = next((x for x in d.fields if x.field_label == label and x.frame_key == frame_key), None)
    if f is None:
        f = DocumentField(document_id=did, field_label=label, frame_key=frame_key,
                          per_frame=bool(frame_key))
        db.add(f)
        d.fields.append(f)
    if f.value != value:
        audit(db, user, "update", "document", did, field=f"字段「{label}」", old=f.value, new=value)
        f.value, f.origin = value, "user"
    db.commit()
    return {"ok": True, "origin": f.origin}


@router.post("/documents/{did}/regenerate")
def regenerate(did: int, body: dict = {}, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """重新生成 AI 初稿（origin=user 字段保留）；随后重渲染。"""
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    doc_generate.generate_values(db, d)
    try:
        doc_generate.render_document(db, d)
    except Exception as e:
        db.commit()
        raise HTTPException(500, f"渲染失败：{e}")
    audit(db, user, "update", "document", did, note="重新生成初稿")
    db.commit()
    return _doc_out(d)


@router.post("/documents/{did}/render")
def render(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    try:
        path = doc_generate.render_document(db, d)
    except Exception as e:
        raise HTTPException(500, f"渲染失败：{e}")
    db.commit()
    return {"ok": True, "file_path": path}


@router.post("/documents/{did}/confirm")
def confirm(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    d.status = "confirmed"
    d.confirmed_at = datetime.now(timezone.utc)
    d.confirmed_by = user.id
    audit(db, user, "confirm", "document", did, field="status", old="draft", new="confirmed",
          note=f"确认文书：{d.name}")
    db.commit()
    return _doc_out(d)


@router.post("/documents/{did}/unconfirm")
def unconfirm(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    d.status = "draft"
    audit(db, user, "status_change", "document", did, field="status", old="confirmed", new="draft",
          note="人工退回初稿")
    db.commit()
    return _doc_out(d)


@router.get("/documents/{did}/export")
def export(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d or not d.file_path:
        raise HTTPException(404, "文书尚未渲染")
    p = MEDIA_DIR.parent / d.file_path
    if not p.exists():
        raise HTTPException(404, "产物文件不存在，请重新渲染")
    return FileResponse(str(p), filename=f"{d.name}.docx")


@router.delete("/documents/{did}")
def delete_document(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, did)
    if not d:
        raise HTTPException(404)
    audit(db, user, "delete", "document", did, note=f"删除文书：{d.name}")
    db.delete(d)
    db.commit()
    return {"ok": True}


@router.get("/documents/{did}/history")
def document_history(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(AuditLog).filter_by(object_type="document", object_id=did)
            .order_by(AuditLog.at.desc()).limit(100).all())
    return [{"id": r.id, "at": r.at.isoformat(), "actor_id": r.actor_id, "action": r.action,
             "detail": r.detail} for r in rows]


class PhotoSelect(BaseModel):
    frame_ids: list[int]


@router.post("/documents/{did}/frames")
def set_frames(did: int, body: PhotoSelect, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """照片文书选帧：调整逐帧字段集合后重新生成值。"""
    d = db.get(Document, did)
    if not d or d.doc_type != "photo":
        raise HTTPException(400, "仅照片文书支持选帧")
    doc_generate.generate_values(db, d, frame_ids=[str(x) for x in body.frame_ids])
    audit(db, user, "update", "document", did, note=f"调整选帧：{sorted(body.frame_ids)}")
    db.commit()
    return _doc_out(d)
