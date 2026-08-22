from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..document_service import mark_document_fields_stale
from ..models import (
    Evidence,
    Material,
    MaterialKind,
    ProcessingStatus,
    ProcessingStep,
    StepKind,
    TranscriptSegment,
    UploadSession,
    User,
)
from ..processing import ensure_material_steps
from ..schemas import MaterialOut, TranscriptOut, TranscriptUpdate, UploadComplete, UploadStart
from ..security import current_user, require_inspection
from ..storage import LocalStorage, get_storage
from ..tasks import dispatch_material

router = APIRouter(tags=["materials"])


def infer_kind(content_type: str, filename: str) -> MaterialKind:
    content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if content_type.startswith("video/"):
        return MaterialKind.VIDEO
    if content_type.startswith("image/"):
        return MaterialKind.IMAGE
    if content_type.startswith("audio/"):
        return MaterialKind.AUDIO
    if content_type.startswith("text/") or filename.lower().endswith(".txt"):
        return MaterialKind.TEXT
    return MaterialKind.OTHER


def material_out(db: Session, material: Material) -> MaterialOut:
    steps = list(
        db.scalars(
            select(ProcessingStep)
            .where(
                ProcessingStep.subject_type == "material", ProcessingStep.subject_id == material.id
            )
            .order_by(ProcessingStep.created_at)
        )
    )
    return MaterialOut.model_validate(material).model_copy(update={"steps": steps})


@router.get("/inspections/{inspection_id}/materials", response_model=list[MaterialOut])
def list_materials(
    inspection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[MaterialOut]:
    require_inspection(db, user, inspection_id)
    materials = db.scalars(
        select(Material)
        .where(Material.inspection_id == inspection_id, Material.deleted_at.is_(None))
        .order_by(Material.created_at.desc())
    )
    return [material_out(db, material) for material in materials]


@router.post("/inspections/{inspection_id}/upload-sessions", status_code=status.HTTP_201_CREATED)
def start_upload(
    inspection_id: uuid.UUID,
    payload: UploadStart,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    require_inspection(db, user, inspection_id)
    if payload.size_bytes > get_settings().max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件超过部署配置的大小限制")
    filename = Path(payload.filename).name
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件名无效")
    object_key = f"inspections/{inspection_id}/original/{uuid.uuid4()}-{filename}"
    storage = get_storage()
    external_id = storage.begin_multipart(object_key, payload.content_type)
    upload = UploadSession(
        inspection_id=inspection_id,
        filename=filename,
        content_type=payload.content_type,
        expected_size=payload.size_bytes,
        expected_sha256=payload.sha256.lower() if payload.sha256 else None,
        object_key=object_key,
        external_upload_id=external_id,
        created_by=user.id,
    )
    db.add(upload)
    db.commit()
    return {
        "id": str(upload.id),
        "part_size": 8 * 1024 * 1024,
        "upload_mode": "proxy" if isinstance(storage, LocalStorage) else "presigned",
    }


def _get_upload(db: Session, user: User, upload_id: uuid.UUID) -> UploadSession:
    upload = db.get(UploadSession, upload_id)
    if not upload:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "上传会话不存在")
    require_inspection(db, user, upload.inspection_id)
    return upload


@router.get("/upload-sessions/{upload_id}/parts/{part_number}")
def presign_upload_part(
    upload_id: uuid.UUID,
    part_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    upload = _get_upload(db, user, upload_id)
    url = get_storage().presign_part(upload.object_key, upload.external_upload_id, part_number)
    return {"url": url, "proxy_required": url is None}


@router.put("/upload-sessions/{upload_id}/parts/{part_number}")
async def upload_part(
    upload_id: uuid.UUID,
    part_number: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    upload = _get_upload(db, user, upload_id)
    if upload.state != "created":
        raise HTTPException(status.HTTP_409_CONFLICT, "上传会话已结束")
    data = await request.body()
    etag = get_storage().put_part(upload.object_key, upload.external_upload_id, part_number, data)
    part = {"part_number": part_number, "etag": etag, "size": len(data)}
    upload.parts = [item for item in upload.parts if item["part_number"] != part_number] + [part]
    db.commit()
    return part


@router.post("/upload-sessions/{upload_id}/complete", response_model=MaterialOut)
def complete_upload(
    upload_id: uuid.UUID,
    payload: UploadComplete,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> MaterialOut:
    upload = _get_upload(db, user, upload_id)
    if upload.state != "created":
        raise HTTPException(status.HTTP_409_CONFLICT, "上传会话已结束")
    parts = payload.parts or upload.parts
    get_storage().complete_multipart(
        upload.object_key, upload.external_upload_id, parts, upload.content_type
    )
    data = get_storage().read_bytes(upload.object_key)
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != upload.expected_size or (
        upload.expected_sha256 and digest != upload.expected_sha256
    ):
        upload.state = "failed"
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "文件大小或 SHA-256 校验失败")
    material = Material(
        inspection_id=upload.inspection_id,
        filename=upload.filename,
        content_type=upload.content_type,
        kind=infer_kind(upload.content_type, upload.filename),
        size_bytes=len(data),
        sha256=digest,
        object_key=upload.object_key,
        created_by=user.id,
    )
    upload.state = "completed"
    db.add(material)
    db.commit()
    ensure_material_steps(db, material)
    dispatch_material(material.id)
    db.refresh(material)
    return material_out(db, material)


@router.get("/materials/{material_id}/download")
def download_material(
    material_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    material = db.get(Material, material_id)
    if not material or material.deleted_at:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "材料不存在")
    require_inspection(db, user, material.inspection_id)
    url = get_storage().presign_get(material.object_key)
    if url:
        return RedirectResponse(url)
    return Response(
        get_storage().read_bytes(material.object_key),
        media_type=material.content_type,
        headers={"Content-Disposition": f'inline; filename="{material.filename}"'},
    )


@router.get("/evidence/{evidence_id}/content")
def evidence_content(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    evidence = db.get(Evidence, evidence_id)
    if not evidence or not evidence.object_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "证据文件不存在")
    require_inspection(db, user, evidence.inspection_id)
    url = get_storage().presign_get(evidence.object_key)
    return RedirectResponse(url) if url else Response(get_storage().read_bytes(evidence.object_key))


@router.post("/materials/{material_id}/steps/{kind}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_step(
    material_id: uuid.UUID,
    kind: StepKind,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    material = db.get(Material, material_id)
    if not material or material.deleted_at:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "材料不存在")
    require_inspection(db, user, material.inspection_id)
    step = db.scalar(
        select(ProcessingStep).where(
            ProcessingStep.subject_type == "material",
            ProcessingStep.subject_id == material.id,
            ProcessingStep.kind == kind,
        )
    )
    if not step:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "处理步骤不存在")
    step.status = ProcessingStatus.PENDING
    step.error_code = None
    step.error_message = None
    db.commit()
    dispatch_material(material.id, kind)
    return {"status": "pending"}


@router.get("/materials/{material_id}/transcript", response_model=list[TranscriptOut])
def list_transcript(
    material_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[TranscriptSegment]:
    material = db.get(Material, material_id)
    if not material:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "材料不存在")
    require_inspection(db, user, material.inspection_id)
    return list(
        db.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.material_id == material.id)
            .order_by(TranscriptSegment.start_ms)
        )
    )


@router.patch("/transcript/{segment_id}", response_model=TranscriptOut)
def update_transcript(
    segment_id: uuid.UUID,
    payload: TranscriptUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> TranscriptSegment:
    segment = db.get(TranscriptSegment, segment_id)
    material = db.get(Material, segment.material_id) if segment else None
    if not segment or not material:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "转写片段不存在")
    require_inspection(db, user, material.inspection_id)
    if segment.row_version != payload.row_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "转写已被修改")
    for key, value in payload.model_dump(exclude={"row_version"}, exclude_unset=True).items():
        setattr(segment, key, value)
    segment.row_version += 1
    mark_document_fields_stale(db, material.inspection_id, "transcript.")
    db.commit()
    return segment
