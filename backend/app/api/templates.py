"""模板上传、自动识别、映射确认（PLAN 阶段 4）。"""
import shutil, uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.config import MEDIA_DIR
from ..core.models import Template, TemplateField, User
from ..core.security import get_current_user, require_admin
from ..core.audit import audit
from ..domain import template_recognize

router = APIRouter(prefix="/api/templates", tags=["templates"])

DOC_TYPES = {"record": "监督检查记录表", "photo": "照片文书", "interrogation": "询问笔录"}


def _field_out(f: TemplateField) -> dict:
    return {"id": f.id, "field_label": f.field_label, "fill_op": f.fill_op, "anchor": f.anchor,
            "confidence": f.confidence, "options": f.options, "shared": f.shared,
            "per_frame": f.per_frame, "enabled": f.enabled, "confirmed": f.confirmed}


def _tpl_out(t: Template, db: Session) -> dict:
    return {"id": t.id, "name": t.name, "doc_type": t.doc_type, "doc_type_name": DOC_TYPES.get(t.doc_type, t.doc_type),
            "filename": t.filename, "version": t.version, "status": t.status, "builtin": t.builtin,
            "warnings": t.warnings, "fields": [_field_out(f) for f in t.fields],
            "created_at": t.created_at.isoformat()}


@router.get("")
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_tpl_out(t, db) for t in db.query(Template).order_by(Template.id)]


@router.post("")
async def upload_template(name: str = Form(...), doc_type: str = Form(...), file: UploadFile = File(...),
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if doc_type not in DOC_TYPES:
        raise HTTPException(400, f"文书类型须为：{list(DOC_TYPES)}")
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "仅支持 .docx 模板")
    rel = Path("media") / "templates" / f"{uuid.uuid4().hex[:12]}.docx"
    dest = MEDIA_DIR.parent / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    t = Template(name=name, doc_type=doc_type, filename=file.filename, file_path=str(rel),
                 status="ready", created_by=user.id)
    db.add(t)
    db.flush()
    # 自动识别（规则引擎，同步执行，毫秒级）
    try:
        fields, warnings = template_recognize.recognize(str(dest), doc_type)
        for f in fields:
            db.add(TemplateField(template_id=t.id, **f,
                                 confirmed=f["confidence"] == "high"))  # 高置信直接采用（PLAN §1.5-5）
        t.warnings = warnings
    except Exception as e:
        t.status = "failed"
        t.warnings = [f"模板识别失败：{e}"]
    audit(db, user, "create", "template", t.id, note=f"上传模板：{name}（{DOC_TYPES[doc_type]}）")
    db.commit()
    return _tpl_out(t, db)


@router.get("/{tid}")
def get_template(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Template, tid)
    if not t:
        raise HTTPException(404)
    return _tpl_out(t, db)


@router.get("/{tid}/file")
def template_file(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Template, tid)
    if not t:
        raise HTTPException(404)
    return FileResponse(str(MEDIA_DIR.parent / t.file_path), filename=t.filename)


class FieldPatch(BaseModel):
    field_label: str | None = None
    fill_op: str | None = None
    enabled: bool | None = None
    confirmed: bool | None = None
    shared: bool | None = None
    per_frame: bool | None = None


@router.patch("/{tid}/fields/{fid}")
def patch_field(tid: int, fid: int, body: FieldPatch, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    f = db.get(TemplateField, fid)
    if not f or f.template_id != tid:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_unset=True).items():
        old = getattr(f, k)
        if old != v:
            audit(db, user, "update", "template_field", fid, field=k, old=str(old), new=str(v))
            setattr(f, k, v)
    db.commit()
    return _field_out(f)


@router.post("/{tid}/fields")
def add_field(tid: int, body: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Template, tid)
    if not t:
        raise HTTPException(404)
    f = TemplateField(template_id=tid, field_label=body["field_label"],
                      fill_op=body.get("fill_op", "append_after_label"),
                      anchor=body.get("anchor", {}), confidence="high",
                      options=body.get("options", {}), confirmed=True)
    db.add(f)
    db.commit()
    return _field_out(f)


@router.delete("/{tid}/fields/{fid}")
def del_field(tid: int, fid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    f = db.get(TemplateField, fid)
    if not f or f.template_id != tid:
        raise HTTPException(404)
    db.delete(f)
    db.commit()
    return {"ok": True}


@router.post("/{tid}/confirm")
def confirm_mapping(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Template, tid)
    if not t:
        raise HTTPException(404)
    n = 0
    for f in t.fields:
        if not f.confirmed:
            f.confirmed = True
            n += 1
    audit(db, user, "confirm", "template", tid, note=f"确认模板映射（{n} 个字段）")
    db.commit()
    return _tpl_out(t, db)


@router.post("/{tid}/re-recognize")
def re_recognize(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """规则升级后重新识别；保留人工确认的映射（按 标签+操作 匹配合并）。"""
    t = db.get(Template, tid)
    if not t:
        raise HTTPException(404)
    fields, warnings = template_recognize.recognize(str(MEDIA_DIR.parent / t.file_path), t.doc_type)
    old = {(f.field_label, f.fill_op): f for f in t.fields}
    db.query(TemplateField).filter_by(template_id=tid).delete()
    db.flush()
    for f in fields:
        prev = old.get((f["field_label"], f["fill_op"]))
        db.add(TemplateField(template_id=tid, **f,
                             confirmed=bool(prev and prev.confirmed) or f["confidence"] == "high"))
    t.warnings = warnings
    t.version += 1
    audit(db, user, "update", "template", tid, note=f"重新识别模板（版本 → v{t.version}）")
    db.commit()
    return _tpl_out(t, db)
