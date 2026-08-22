from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import accessible_inspection, get_current_user, model_dict
from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import LocalStorage, get_storage
from app.models import Asset, DocumentDraft, Evidence, FactEvidence, Job, User, new_id
from app.services.audit import audit
from app.services.jobs import create_job
from app.services.media import extract_qa_candidates

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


@router.post("/assets/{asset_id}/extract-qa")
def extract_asset_qa(
    asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "材料不存在")
    accessible_inspection(db, user, asset.inspection_id)
    if asset.kind not in {"audio", "video"}:
        raise HTTPException(422, "只有音频或视频材料可以提取问答")
    transcripts = db.scalars(
        select(Evidence).where(Evidence.asset_id == asset.id, Evidence.kind == "transcript")
    ).all()
    if not transcripts:
        raise HTTPException(422, "该材料尚无语音转写，请先完成材料处理")
    if not get_settings().ai_enabled:
        raise HTTPException(422, "未配置聊天模型，无法整理询问问答")
    count = extract_qa_candidates(db, asset, transcripts)
    audit(
        db,
        actor_id=user.id,
        action="asset.qa_extracted",
        target_type="asset",
        target_id=asset.id,
        inspection_id=asset.inspection_id,
        details={"candidate_count": count},
    )
    db.commit()
    return {"candidate_count": count}


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(
    asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    asset = db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "材料不存在")
    accessible_inspection(db, user, asset.inspection_id)
    if asset.processing_status in {"pending", "processing"}:
        raise HTTPException(409, "材料正在处理中，请等待处理结束后再删除")

    evidence = db.scalars(select(Evidence).where(Evidence.asset_id == asset.id)).all()
    evidence_ids = {item.id for item in evidence}
    if evidence_ids and db.scalar(
        select(FactEvidence).where(FactEvidence.evidence_id.in_(evidence_ids)).limit(1)
    ):
        raise HTTPException(409, "材料证据已被检查事实引用，不能删除")
    for draft in db.scalars(
        select(DocumentDraft).where(DocumentDraft.inspection_id == asset.inspection_id)
    ).all():
        if any(
            evidence_ids.intersection(state.get("evidence_ids", []))
            for state in draft.fields_json.values()
            if isinstance(state, dict)
        ):
            raise HTTPException(409, "材料证据已被文书引用，不能删除")

    storage = get_storage()
    for key in [*(item.storage_key for item in evidence if item.storage_key), asset.storage_key]:
        storage.delete(key)
    if evidence_ids:
        db.execute(delete(FactEvidence).where(FactEvidence.evidence_id.in_(evidence_ids)))
        db.execute(delete(Evidence).where(Evidence.id.in_(evidence_ids)))
    db.execute(delete(Job).where(Job.target_type == "asset", Job.target_id == asset.id))
    audit(
        db,
        actor_id=user.id,
        action="asset.deleted",
        target_type="asset",
        target_id=asset.id,
        inspection_id=asset.inspection_id,
        details={"filename": asset.filename, "processing_status": asset.processing_status},
    )
    db.delete(asset)
    db.commit()


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
        )
    return RedirectResponse(storage.presigned_url(asset.storage_key))


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
