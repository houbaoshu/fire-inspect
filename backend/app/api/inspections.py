"""检查管理 + 材料上传 + 任务状态（PLAN 阶段 1）。"""
import shutil, uuid
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.db import get_db
from ..core.config import MEDIA_DIR, settings
from ..core.models import Inspection, Material, Job, User
from ..core.security import get_current_user
from ..core.audit import audit
from ..domain import media as media_utils
from ..workers.worker import enqueue_media_pipeline, enqueue

router = APIRouter(prefix="/api", tags=["inspections"])

VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class InspectionIn(BaseModel):
    unit_name: str = ""
    address: str = ""
    inspected_at: datetime | None = None
    checker_names: str = ""
    contact: str = ""
    legal_person: str = ""
    check_type: str = ""
    notes: str = ""


def _out(i: Inspection) -> dict:
    return {"id": i.id, "unit_name": i.unit_name, "address": i.address,
            "inspected_at": i.inspected_at.isoformat() if i.inspected_at else None,
            "checker_names": i.checker_names, "contact": i.contact, "legal_person": i.legal_person,
            "check_type": i.check_type, "notes": i.notes,
            "created_at": i.created_at.isoformat()}


@router.get("/inspections")
def list_inspections(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Inspection).order_by(Inspection.id.desc()).all()
    return [_out(i) for i in rows]


@router.post("/inspections")
def create_inspection(body: InspectionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    i = Inspection(**body.model_dump(), created_by=user.id)
    db.add(i)
    db.flush()
    audit(db, user, "create", "inspection", i.id, note=f"创建检查：{i.unit_name}")
    db.commit()
    return _out(i)


@router.get("/inspections/{iid}")
def get_inspection(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    i = db.get(Inspection, iid)
    if not i:
        raise HTTPException(404, "检查不存在")
    return _out(i)


@router.patch("/inspections/{iid}")
def update_inspection(iid: int, body: InspectionIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    i = db.get(Inspection, iid)
    if not i:
        raise HTTPException(404, "检查不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        old = getattr(i, k)
        if old != v:
            audit(db, user, "update", "inspection", iid, field=k,
                  old=str(old) if old else "", new=str(v) if v else "")
            setattr(i, k, v)
    db.commit()
    return _out(i)


@router.delete("/inspections/{iid}")
def delete_inspection(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    i = db.get(Inspection, iid)
    if not i:
        raise HTTPException(404, "检查不存在")
    audit(db, user, "delete", "inspection", iid, note=f"删除检查：{i.unit_name}")
    for m in db.query(Material).filter_by(inspection_id=iid):
        p = MEDIA_DIR.parent / m.file_path
        p.unlink(missing_ok=True)
    db.delete(i)
    db.commit()
    return {"ok": True}


# ---------- 材料 ----------

def _material_out(m: Material) -> dict:
    return {"id": m.id, "inspection_id": m.inspection_id, "type": m.type, "filename": m.filename,
            "size": m.size, "duration_ms": m.duration_ms, "created_at": m.created_at.isoformat()}


@router.get("/inspections/{iid}/materials")
def list_materials(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_material_out(m) for m in db.query(Material).filter_by(inspection_id=iid).order_by(Material.id)]


@router.post("/inspections/{iid}/materials")
async def upload_material(iid: int, file: UploadFile = File(...),
                          db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    insp = db.get(Inspection, iid)
    if not insp:
        raise HTTPException(404, "检查不存在")
    ext = Path(file.filename or "").suffix.lower()
    mtype = ("video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT
             else "photo" if ext in PHOTO_EXT else "other")
    rel = Path("media") / f"i{iid}" / f"{uuid.uuid4().hex[:12]}{ext}"
    dest = MEDIA_DIR.parent / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    m = Material(inspection_id=iid, type=mtype, filename=file.filename or "unnamed",
                 file_path=str(rel), size=dest.stat().st_size, created_by=user.id)
    if mtype in ("video", "audio"):
        try:
            m.duration_ms = media_utils.duration_ms(str(dest))
        except Exception:
            pass
    db.add(m)
    db.flush()
    audit(db, user, "create", "material", m.id, note=f"上传材料：{m.filename}")
    enqueue_media_pipeline(db, m)
    db.commit()
    return _material_out(m)


@router.get("/materials/{mid}/file")
def material_file(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.get(Material, mid)
    if not m:
        raise HTTPException(404)
    return FileResponse(str(MEDIA_DIR.parent / m.file_path), filename=m.filename)


@router.delete("/materials/{mid}")
def delete_material(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.get(Material, mid)
    if not m:
        raise HTTPException(404)
    (MEDIA_DIR.parent / m.file_path).unlink(missing_ok=True)
    audit(db, user, "delete", "material", mid, note=f"删除材料：{m.filename}")
    db.delete(m)
    db.commit()
    return {"ok": True}


# ---------- 任务 ----------

def _job_out(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "status": j.status, "error": j.error,
            "retry_count": j.retry_count,
            "created_at": j.created_at.isoformat(),
            "finished_at": j.finished_at.isoformat() if j.finished_at else None}


@router.get("/inspections/{iid}/jobs")
def list_jobs(iid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_job_out(j) for j in db.query(Job).filter_by(inspection_id=iid).order_by(Job.id.desc()).limit(50)]


@router.get("/materials/{mid}/jobs")
def material_jobs(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [_job_out(j) for j in db.query(Job).filter_by(material_id=mid).order_by(Job.id)]


@router.post("/jobs/{jid}/retry")
def retry_job(jid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    j = db.get(Job, jid)
    if not j:
        raise HTTPException(404)
    if j.status not in ("failed",):
        raise HTTPException(400, "仅失败任务可重试")
    j.status, j.error = "pending", ""
    j.retry_count += 1
    audit(db, user, "update", "job", jid, note=f"重试任务 {j.kind}")
    db.commit()
    return _job_out(j)


@router.post("/materials/{mid}/extract-facts")
def trigger_fact_extract(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.get(Material, mid)
    if not m:
        raise HTTPException(404)
    j = enqueue(db, m.inspection_id, "fact_extract", mid)
    db.commit()
    return _job_out(j)
