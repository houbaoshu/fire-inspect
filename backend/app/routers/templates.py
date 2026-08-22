import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..database import get_db
from ..models import (
    DocumentType,
    ProcessingStatus,
    Template,
    TemplateRegion,
    TemplateVersion,
    User,
)
from ..schemas import (
    TemplateOut,
    TemplateRegionOut,
    TemplateRegionUpdate,
    TemplateVersionOut,
)
from ..security import current_user, require_admin
from ..storage import get_storage
from ..tasks import dispatch_template
from ..template_service import normalize_word, render_docx_preview

router = APIRouter(prefix="/templates", tags=["templates"])


def version_out(db: Session, version: TemplateVersion) -> TemplateVersionOut:
    regions = list(
        db.scalars(
            select(TemplateRegion)
            .where(TemplateRegion.template_version_id == version.id)
            .order_by(TemplateRegion.ordinal)
        )
    )
    return TemplateVersionOut.model_validate(version).model_copy(update={"regions": regions})


@router.get("", response_model=list[TemplateOut])
def list_templates(
    db: Session = Depends(get_db), _: User = Depends(current_user)
) -> list[Template]:
    return list(db.scalars(select(Template).order_by(Template.created_at.desc())))


@router.post("", response_model=TemplateVersionOut, status_code=status.HTTP_201_CREATED)
async def upload_template(
    name: str = Form(...),
    document_type: DocumentType = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TemplateVersionOut:
    filename = file.filename or "template.docx"
    if not filename.lower().endswith((".docx", ".doc")):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "模板仅支持 DOCX 或 DOC")
    data = await file.read()
    try:
        normalized = normalize_word(data, filename)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    template = Template(name=name, document_type=document_type, is_public=True, created_by=user.id)
    db.add(template)
    db.flush()
    original_key = f"templates/{template.id}/v1/original-{filename}"
    normalized_key = f"templates/{template.id}/v1/template.docx"
    storage = get_storage()
    storage.put_bytes(original_key, data, file.content_type or "application/msword")
    storage.put_bytes(
        normalized_key,
        normalized,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    preview = render_docx_preview(normalized)
    preview_key = None
    if preview:
        preview_key = f"templates/{template.id}/v1/preview.pdf"
        storage.put_bytes(preview_key, preview, "application/pdf")
    version = TemplateVersion(
        template_id=template.id,
        version_number=1,
        original_filename=filename,
        original_object_key=original_key,
        normalized_object_key=normalized_key,
        preview_object_key=preview_key,
        sha256=hashlib.sha256(normalized).hexdigest(),
    )
    db.add(version)
    db.flush()
    template.active_version_id = version.id
    db.commit()
    dispatch_template(version.id)
    db.refresh(version)
    return version_out(db, version)


@router.get("/{template_id}/versions/{version_id}", response_model=TemplateVersionOut)
def get_template_version(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> TemplateVersionOut:
    version = db.scalar(
        select(TemplateVersion).where(
            TemplateVersion.id == version_id, TemplateVersion.template_id == template_id
        )
    )
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板版本不存在")
    return version_out(db, version)


@router.patch("/regions/{region_id}", response_model=TemplateRegionOut)
def update_region(
    region_id: uuid.UUID,
    payload: TemplateRegionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TemplateRegion:
    region = db.get(TemplateRegion, region_id)
    if not region:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板区域不存在")
    if region.row_version != payload.row_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "模板映射已被其他用户修改")
    before = {
        "label": region.label,
        "source_path": region.source_path,
        "confirmed": region.confirmed,
    }
    for key, value in payload.model_dump(exclude={"row_version"}, exclude_unset=True).items():
        setattr(region, key, value)
    region.row_version += 1
    record_audit(
        db,
        user,
        "template.region_updated",
        "template_region",
        region.id,
        before,
        payload.model_dump(exclude_unset=True, mode="json"),
    )
    db.commit()
    return region


@router.post("/{template_id}/versions/{version_id}/confirm", response_model=TemplateVersionOut)
def confirm_mapping(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> TemplateVersionOut:
    version = db.scalar(
        select(TemplateVersion).where(
            TemplateVersion.id == version_id, TemplateVersion.template_id == template_id
        )
    )
    if not version or version.status != ProcessingStatus.SUCCEEDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "模板尚未成功完成识别")
    confirmed_count = db.scalar(
        select(func.count(TemplateRegion.id)).where(
            TemplateRegion.template_version_id == version.id, TemplateRegion.confirmed.is_(True)
        )
    )
    if not confirmed_count:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "至少确认一个填充区域")
    version.mapping_confirmed = True
    record_audit(
        db,
        user,
        "template.mapping_confirmed",
        "template_version",
        version.id,
        after={"confirmed_regions": confirmed_count},
    )
    db.commit()
    return version_out(db, version)


@router.post("/{template_id}/versions/{version_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_analysis(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    version = db.scalar(
        select(TemplateVersion).where(
            TemplateVersion.id == version_id, TemplateVersion.template_id == template_id
        )
    )
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板版本不存在")
    version.status = ProcessingStatus.PENDING
    version.error_message = None
    version.mapping_confirmed = False
    db.commit()
    dispatch_template(version.id)
    return {"status": "pending"}


@router.get("/{template_id}/versions/{version_id}/preview")
def template_preview(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    version = db.scalar(
        select(TemplateVersion).where(
            TemplateVersion.id == version_id, TemplateVersion.template_id == template_id
        )
    )
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板版本不存在")
    key = version.preview_object_key or version.normalized_object_key
    content_type = (
        "application/pdf"
        if version.preview_object_key
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    url = get_storage().presign_get(key)
    return (
        RedirectResponse(url)
        if url
        else Response(get_storage().read_bytes(key), media_type=content_type)
    )
