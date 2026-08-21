from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_inspection, get_current_user, model_dict
from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import LocalStorage, get_storage
from app.models import Asset, Evidence, User, new_id
from app.services.audit import audit
from app.services.jobs import create_job

router = APIRouter(tags=["材料与证据"])

ALLOWED_EXTENSIONS = {
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".txt": "text",
}


def serialize_asset(item: Asset) -> dict:
    return model_dict(
        item,
        "id",
        "inspection_id",
        "kind",
        "filename",
        "content_type",
        "processing_status",
        "processing_error",
        "metadata_json",
        "created_at",
        "updated_at",
    )


def serialize_evidence(item: Evidence) -> dict:
    data = model_dict(
        item,
        "id",
        "inspection_id",
        "asset_id",
        "kind",
        "start_ms",
        "end_ms",
        "excerpt",
        "metadata_json",
        "created_at",
    )
    data["has_file"] = bool(item.storage_key)
    return data


@router.get("/inspections/{inspection_id}/assets")
def list_assets(
    inspection_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    accessible_inspection(db, user, inspection_id)
    return [
        serialize_asset(item)
        for item in db.scalars(
            select(Asset).where(Asset.inspection_id == inspection_id).order_by(Asset.created_at)
        ).all()
    ]


@router.post("/inspections/{inspection_id}/assets", status_code=201)
def upload_asset(
    inspection_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accessible_inspection(db, user, inspection_id)
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    kind = ALLOWED_EXTENSIONS.get(suffix)
    if not kind:
        raise HTTPException(415, "仅支持 MP4/MOV、MP3/WAV/M4A、JPG/PNG 和 TXT")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > get_settings().max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"文件不能超过 {get_settings().max_upload_mb} MB")
    asset_id = new_id()
    key = f"inspections/{inspection_id}/assets/{asset_id}{suffix}"
    get_storage().put_fileobj(key, file.file, file.content_type)
    asset = Asset(
        id=asset_id,
        inspection_id=inspection_id,
        kind=kind,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        storage_key=key,
        uploaded_by=user.id,
    )
    db.add(asset)
    db.flush()
    audit(
        db,
        actor_id=user.id,
        action="asset.uploaded",
        target_type="asset",
        target_id=asset.id,
        inspection_id=inspection_id,
        details={"filename": filename},
    )
    db.commit()
    job = create_job(db, kind="process_asset", target_type="asset", target_id=asset.id)
    db.refresh(asset)
    return {"asset": serialize_asset(asset), "job_id": job.id}


@router.post("/assets/{asset_id}/retry")
def retry_asset(
    asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "材料不存在")
    accessible_inspection(db, user, asset.inspection_id)
    asset.processing_status = "pending"
    asset.processing_error = None
    db.commit()
    job = create_job(db, kind="process_asset", target_type="asset", target_id=asset.id)
    return {"job_id": job.id}


@router.get("/assets/{asset_id}/content")
def asset_content(
    asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "材料不存在")
    accessible_inspection(db, user, asset.inspection_id)
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        return FileResponse(
            storage.local_path(asset.storage_key),
            media_type=asset.content_type,
            filename=asset.filename,
        )
    return RedirectResponse(storage.presigned_url(asset.storage_key, asset.filename))


@router.get("/inspections/{inspection_id}/evidence")
def list_evidence(
    inspection_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    accessible_inspection(db, user, inspection_id)
    return [
        serialize_evidence(item)
        for item in db.scalars(
            select(Evidence)
            .where(Evidence.inspection_id == inspection_id)
            .order_by(Evidence.created_at)
        ).all()
    ]


@router.get("/evidence/{evidence_id}/content")
def evidence_content(
    evidence_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    evidence = db.get(Evidence, evidence_id)
    if not evidence or not evidence.storage_key:
        raise HTTPException(404, "证据文件不存在")
    accessible_inspection(db, user, evidence.inspection_id)
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        return FileResponse(storage.local_path(evidence.storage_key))
    return RedirectResponse(storage.presigned_url(evidence.storage_key))
