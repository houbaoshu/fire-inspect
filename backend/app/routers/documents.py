import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..database import get_db
from ..document_service import (
    confirm_document,
    create_document,
    document_photo_evidence_ids,
    export_document,
    generated_region_value,
    is_evidence_chain_source,
    is_shared_document_source,
    set_shared_inspection_value,
    source_value,
    synchronize_shared_document_fields,
)
from ..models import (
    DocumentDraft,
    DocumentField,
    DocumentStatus,
    Inspection,
    TemplateRegion,
    TemplateVersion,
    User,
    ValueSource,
)
from ..schemas import (
    DocumentCreate,
    DocumentFieldOut,
    DocumentFieldUpdate,
    DocumentOut,
    RegenerateAccept,
    RegenerateIn,
    RegenerateProposal,
)
from ..security import current_user, require_inspection

router = APIRouter(tags=["documents"])


def document_out(db: Session, document: DocumentDraft) -> DocumentOut:
    rows = db.execute(
        select(DocumentField, TemplateRegion)
        .join(TemplateRegion, TemplateRegion.id == DocumentField.region_id)
        .where(DocumentField.document_id == document.id)
        .order_by(DocumentField.created_at)
    ).all()
    fields = [
        DocumentFieldOut.model_validate(field).model_copy(
            update={
                "label": region.label,
                "field_type": region.field_type,
                "source_path": region.source_path,
                "is_shared": is_shared_document_source(region.source_path),
                "is_evidence_linked": is_evidence_chain_source(region.source_path),
            }
        )
        for field, region in rows
    ]
    return DocumentOut.model_validate(document).model_copy(update={"fields": fields})


def _get_document(db: Session, user: User, document_id: uuid.UUID) -> DocumentDraft:
    document = db.get(DocumentDraft, document_id)
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文书不存在")
    require_inspection(db, user, document.inspection_id)
    return document


@router.get("/inspections/{inspection_id}/documents", response_model=list[DocumentOut])
def list_documents(
    inspection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[DocumentOut]:
    require_inspection(db, user, inspection_id)
    documents = db.scalars(
        select(DocumentDraft)
        .where(DocumentDraft.inspection_id == inspection_id)
        .order_by(DocumentDraft.updated_at.desc())
    )
    return [document_out(db, item) for item in documents]


@router.post("/inspections/{inspection_id}/documents", response_model=DocumentOut)
def generate_document(
    inspection_id: uuid.UUID,
    payload: DocumentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DocumentOut:
    inspection = require_inspection(db, user, inspection_id)
    version = db.get(TemplateVersion, payload.template_version_id)
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模板版本不存在")
    try:
        document = create_document(
            db, inspection, version, payload.title, payload.selected_evidence_ids, user
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    record_audit(db, user, "document.generated", "document", document.id)
    db.commit()
    return document_out(db, document)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DocumentOut:
    return document_out(db, _get_document(db, user, document_id))


@router.patch("/document-fields/{field_id}", response_model=DocumentOut)
def update_field(
    field_id: uuid.UUID,
    payload: DocumentFieldUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DocumentOut:
    field = db.get(DocumentField, field_id)
    document = _get_document(db, user, field.document_id) if field else None
    if not field or not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文书字段不存在")
    if field.row_version != payload.row_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "文书字段已被其他用户修改")
    region = db.get(TemplateRegion, field.region_id)
    inspection = db.get(Inspection, document.inspection_id)
    shared_source = region.source_path if region else None
    before = {"value": field.value, "row_version": field.row_version}
    affected_documents: set[uuid.UUID] = set()
    if inspection and is_shared_document_source(shared_source):
        shared_before = source_value(db, inspection, shared_source)
        try:
            set_shared_inspection_value(inspection, shared_source, payload.value)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        shared_value = source_value(db, inspection, shared_source)
        affected_documents = synchronize_shared_document_fields(
            db,
            inspection,
            shared_source,
            shared_value,
            human_edit=True,
        )
        record_audit(
            db,
            user,
            "inspection.shared_field_updated",
            "inspection",
            inspection.id,
            {"source_path": shared_source, "value": shared_before},
            {"source_path": shared_source, "value": shared_value},
        )
    else:
        field.value = payload.value
        field.source = ValueSource.HUMAN
        field.is_locked = True
        field.is_stale = False
        field.row_version += 1
        if document.status == DocumentStatus.CONFIRMED:
            document.status = DocumentStatus.DRAFT
            document.confirmed_by = None
            document.confirmed_at = None
        document.row_version += 1
    record_audit(
        db,
        user,
        "document.field_updated",
        "document_field",
        field.id,
        before,
        {
            "value": payload.value,
            "locked": True,
            "shared_source": shared_source if affected_documents else None,
            "affected_documents": [str(item) for item in sorted(affected_documents, key=str)],
        },
    )
    db.commit()
    return document_out(db, document)


@router.post("/documents/{document_id}/regenerate", response_model=list[RegenerateProposal])
def regenerate_fields(
    document_id: uuid.UUID,
    payload: RegenerateIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[RegenerateProposal]:
    document = _get_document(db, user, document_id)
    inspection = db.get(Inspection, document.inspection_id)
    rows = db.execute(
        select(DocumentField, TemplateRegion)
        .join(TemplateRegion, TemplateRegion.id == DocumentField.region_id)
        .where(DocumentField.document_id == document.id, DocumentField.id.in_(payload.field_ids))
    ).all()
    selected_evidence_ids = document_photo_evidence_ids(db, document.id)
    return [
        RegenerateProposal(
            field_id=field.id,
            current_value=field.value,
            proposed_value=generated_region_value(
                db,
                inspection,
                document.document_type,
                region,
                selected_evidence_ids,
            ),
            locked=field.is_locked,
        )
        for field, region in rows
    ]


@router.post("/documents/{document_id}/regenerate/accept", response_model=DocumentOut)
def accept_regeneration(
    document_id: uuid.UUID,
    payload: RegenerateAccept,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DocumentOut:
    document = _get_document(db, user, document_id)
    for proposal in payload.proposals:
        field_id = uuid.UUID(str(proposal["field_id"]))
        field = db.scalar(
            select(DocumentField).where(
                DocumentField.id == field_id, DocumentField.document_id == document.id
            )
        )
        if not field:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "提案包含无效字段")
        field.value = proposal.get("proposed_value")
        field.source = ValueSource.GENERATED
        field.is_locked = False
        field.is_stale = False
        field.row_version += 1
    document.status = DocumentStatus.DRAFT
    document.confirmed_by = None
    document.confirmed_at = None
    document.row_version += 1
    record_audit(db, user, "document.regeneration_accepted", "document", document.id)
    db.commit()
    return document_out(db, document)


@router.post("/documents/{document_id}/confirm", response_model=DocumentOut)
def confirm(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DocumentOut:
    document = _get_document(db, user, document_id)
    confirm_document(db, document, user)
    record_audit(db, user, "document.confirmed", "document", document.id)
    db.commit()
    return document_out(db, document)


@router.post("/documents/{document_id}/export")
def export(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Response:
    document = _get_document(db, user, document_id)
    try:
        version, output = export_document(db, document, user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    record_audit(
        db,
        user,
        "document.exported",
        "document",
        document.id,
        after={"version": version.version_number, "warnings": version.warnings},
    )
    db.commit()
    return Response(
        output,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f'attachment; filename="document-{document.id}-v{version.version_number}.docx"'
            ),
            "X-Document-Warnings": str(len(version.warnings)),
        },
    )
