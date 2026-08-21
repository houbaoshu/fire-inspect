import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import accessible_inspection, get_current_user, model_dict
from app.core.database import get_db
from app.core.storage import LocalStorage, get_storage
from app.models import DocumentDraft, DocumentTemplate, Evidence, User, new_id, utcnow
from app.services.audit import audit
from app.services.documents import (
    build_document_fields,
    default_field_manifest,
    render_document,
    scan_template_fields,
)

router = APIRouter(tags=["文书"])
DOCUMENT_TYPES = {"inspection_record", "photo_record", "inquiry_record"}


def serialize_template(item: DocumentTemplate) -> dict:
    return model_dict(
        item,
        "id",
        "owner_id",
        "name",
        "document_type",
        "fields_json",
        "builtin",
        "created_at",
        "updated_at",
    )


def serialize_document(item: DocumentDraft) -> dict:
    return model_dict(
        item,
        "id",
        "inspection_id",
        "template_id",
        "document_type",
        "status",
        "fields_json",
        "confirmed_by",
        "confirmed_at",
        "created_at",
        "updated_at",
    )


@router.get("/templates")
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    templates = db.scalars(
        select(DocumentTemplate)
        .where(or_(DocumentTemplate.owner_id.is_(None), DocumentTemplate.owner_id == user.id))
        .order_by(DocumentTemplate.builtin.desc(), DocumentTemplate.name)
    ).all()
    return [serialize_template(item) for item in templates]


@router.post("/templates", status_code=201)
def upload_template(
    name: str = Form(...),
    document_type: str = Form(...),
    fields_json: str | None = Form(default=None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "template.docx").name
    if Path(filename).suffix.lower() != ".docx":
        raise HTTPException(415, "模板必须是 DOCX")
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(422, "不支持的文书类型")
    content = file.file.read()
    try:
        codes = scan_template_fields(content)
        parsed_manifest = json.loads(fields_json) if fields_json else default_field_manifest(codes)
        if not isinstance(parsed_manifest, list):
            raise ValueError("字段清单必须是数组")
        manifest = [
            TemplateFieldInput.model_validate(item).model_dump() for item in parsed_manifest
        ]
    except (ValueError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"模板格式无效：{exc}") from exc
    if not codes:
        raise HTTPException(422, "模板中没有找到 {{ field_code }} 占位符")
    manifest_codes = {item.get("code") for item in manifest}
    if manifest_codes != set(codes) or len(manifest_codes) != len(manifest):
        raise HTTPException(422, "字段清单必须与模板占位符完全一致")
    if any(
        item.get("type") not in {"text", "multiline", "date", "image", "photo_list"}
        for item in manifest
    ):
        raise HTTPException(422, "存在不支持的字段类型")
    template_id = new_id()
    key = f"templates/{user.id}/{template_id}.docx"
    get_storage().put_bytes(key, content, file.content_type)
    template = DocumentTemplate(
        id=template_id,
        owner_id=user.id,
        name=name,
        document_type=document_type,
        storage_key=key,
        fields_json=manifest,
    )
    db.add(template)
    db.commit()
    return serialize_template(template)


class DraftCreate(BaseModel):
    template_id: str


class TemplateFieldInput(BaseModel):
    code: str
    label: str = Field(min_length=1, max_length=100)
    type: str
    description: str = Field(default="", max_length=300)


class TemplateFieldsUpdate(BaseModel):
    fields: list[TemplateFieldInput]


@router.patch("/templates/{template_id}/fields")
def update_template_fields(
    template_id: str,
    payload: TemplateFieldsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    template = db.get(DocumentTemplate, template_id)
    if not template or template.owner_id != user.id or template.builtin:
        raise HTTPException(404, "可编辑模板不存在")
    current_codes = {item["code"] for item in template.fields_json}
    new_codes = {item.code for item in payload.fields}
    if current_codes != new_codes or len(new_codes) != len(payload.fields):
        raise HTTPException(422, "不能新增、删除或重命名模板占位符")
    supported = {"text", "multiline", "date", "image", "photo_list"}
    if any(item.type not in supported for item in payload.fields):
        raise HTTPException(422, "存在不支持的字段类型")
    template.fields_json = [item.model_dump() for item in payload.fields]
    db.commit()
    return serialize_template(template)


@router.get("/inspections/{inspection_id}/documents")
def list_documents(
    inspection_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    accessible_inspection(db, user, inspection_id)
    return [
        serialize_document(item)
        for item in db.scalars(
            select(DocumentDraft)
            .where(DocumentDraft.inspection_id == inspection_id)
            .order_by(DocumentDraft.created_at)
        ).all()
    ]


@router.post("/inspections/{inspection_id}/documents", status_code=201)
def create_document(
    inspection_id: str,
    payload: DraftCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inspection = accessible_inspection(db, user, inspection_id)
    template = db.get(DocumentTemplate, payload.template_id)
    if not template or (template.owner_id and template.owner_id != user.id):
        raise HTTPException(404, "模板不存在")
    draft = DocumentDraft(
        inspection_id=inspection.id,
        template_id=template.id,
        document_type=template.document_type,
        fields_json=build_document_fields(db, inspection, template),
    )
    db.add(draft)
    db.flush()
    audit(
        db,
        actor_id=user.id,
        action="document.created",
        target_type="document",
        target_id=draft.id,
        inspection_id=inspection.id,
    )
    db.commit()
    return serialize_document(draft)


def _owned_draft(db: Session, user: User, document_id: str) -> DocumentDraft:
    draft = db.get(DocumentDraft, document_id)
    if not draft:
        raise HTTPException(404, "文书不存在")
    accessible_inspection(db, user, draft.inspection_id)
    return draft


class FieldUpdate(BaseModel):
    value: Any


@router.patch("/documents/{document_id}/fields/{field_code}")
def update_field(
    document_id: str,
    field_code: str,
    payload: FieldUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = _owned_draft(db, user, document_id)
    fields = dict(draft.fields_json)
    if field_code not in fields:
        raise HTTPException(404, "字段不存在")
    template = db.get(DocumentTemplate, draft.template_id)
    manifest = {item["code"]: item for item in template.fields_json} if template else {}
    field_type = manifest.get(field_code, {}).get("type", "text")
    selected_evidence_ids: list[str] = []
    if field_type == "image":
        if payload.value not in (None, "") and not isinstance(payload.value, str):
            raise HTTPException(422, "单张图片字段必须选择一条图片证据")
        selected_evidence_ids = [payload.value] if payload.value else []
    elif field_type == "photo_list":
        if not isinstance(payload.value, list) or any(
            not isinstance(item, str) for item in payload.value
        ):
            raise HTTPException(422, "照片列表字段必须是图片证据 ID 列表")
        selected_evidence_ids = list(dict.fromkeys(payload.value))
    if field_type in {"image", "photo_list"} and selected_evidence_ids:
        selected_evidence = db.scalars(
            select(Evidence).where(
                Evidence.id.in_(selected_evidence_ids),
                Evidence.inspection_id == draft.inspection_id,
                Evidence.kind.in_(["keyframe", "photo"]),
                Evidence.storage_key.is_not(None),
            )
        ).all()
        if {item.id for item in selected_evidence} != set(selected_evidence_ids):
            raise HTTPException(422, "只能选择当前检查中的关键帧或照片")
    state = dict(fields[field_code])
    value = selected_evidence_ids if field_type == "photo_list" else payload.value
    state.update(value=value, edited=True, stale=False)
    if field_type in {"image", "photo_list"}:
        state["evidence_ids"] = selected_evidence_ids
    fields[field_code] = state
    draft.fields_json = fields
    if draft.status == "confirmed":
        draft.status = "draft"
        draft.confirmed_by = None
        draft.confirmed_at = None
        audit(
            db,
            actor_id=user.id,
            action="document.reopened",
            target_type="document",
            target_id=draft.id,
            inspection_id=draft.inspection_id,
        )
    audit(
        db,
        actor_id=user.id,
        action="document.field_updated",
        target_type="document",
        target_id=draft.id,
        inspection_id=draft.inspection_id,
        details={"field": field_code},
    )
    db.commit()
    return serialize_document(draft)


class RegenerateInput(BaseModel):
    field_codes: list[str] = Field(min_length=1)


@router.post("/documents/{document_id}/regenerate")
def regenerate_fields(
    document_id: str,
    payload: RegenerateInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    draft = _owned_draft(db, user, document_id)
    inspection = accessible_inspection(db, user, draft.inspection_id)
    template = db.get(DocumentTemplate, draft.template_id)
    regenerated = build_document_fields(db, inspection, template)
    fields = dict(draft.fields_json)
    for code in payload.field_codes:
        if code not in fields:
            raise HTTPException(404, f"字段不存在：{code}")
        fields[code] = regenerated[code]
    draft.fields_json = fields
    draft.status = "draft"
    draft.confirmed_by = None
    draft.confirmed_at = None
    audit(
        db,
        actor_id=user.id,
        action="document.fields_regenerated",
        target_type="document",
        target_id=draft.id,
        inspection_id=draft.inspection_id,
        details={"fields": payload.field_codes},
    )
    db.commit()
    return serialize_document(draft)


@router.post("/documents/{document_id}/confirm")
def confirm_document(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    draft = _owned_draft(db, user, document_id)
    stale = [code for code, state in draft.fields_json.items() if state.get("stale")]
    if stale:
        raise HTTPException(422, {"message": "存在需要更新的字段", "fields": stale})
    draft.status = "confirmed"
    draft.confirmed_by = user.id
    draft.confirmed_at = utcnow()
    audit(
        db,
        actor_id=user.id,
        action="document.confirmed",
        target_type="document",
        target_id=draft.id,
        inspection_id=draft.inspection_id,
    )
    db.commit()
    return serialize_document(draft)


@router.post("/documents/{document_id}/export")
def export_document(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    draft = _owned_draft(db, user, document_id)
    if draft.status != "confirmed":
        raise HTTPException(422, "文书必须确认后才能导出")
    content = render_document(db, draft)
    key = f"inspections/{draft.inspection_id}/documents/{draft.id}.docx"
    get_storage().put_bytes(
        key, content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    draft.export_storage_key = key
    audit(
        db,
        actor_id=user.id,
        action="document.exported",
        target_type="document",
        target_id=draft.id,
        inspection_id=draft.inspection_id,
    )
    db.commit()
    return {"download_url": f"/api/documents/{draft.id}/download"}


@router.get("/documents/{document_id}/download")
def download_document(
    document_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    draft = _owned_draft(db, user, document_id)
    if not draft.export_storage_key:
        raise HTTPException(404, "尚未生成导出文件")
    storage = get_storage()
    filename = f"{draft.document_type}-{draft.id[:8]}.docx"
    if isinstance(storage, LocalStorage):
        return FileResponse(storage.local_path(draft.export_storage_key), filename=filename)
    return RedirectResponse(storage.presigned_url(draft.export_storage_key, filename))
