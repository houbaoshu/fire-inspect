import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..knowledge_service import search_knowledge
from ..models import (
    KnowledgeCategory,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
    ProcessingStatus,
    ProcessingStep,
    User,
)
from ..schemas import KnowledgeOut, SearchIn, SearchOut
from ..security import current_user, require_admin
from ..storage import get_storage
from ..tasks import dispatch_knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def knowledge_out(db: Session, document: KnowledgeDocument) -> KnowledgeOut:
    latest = db.scalar(
        select(KnowledgeDocumentVersion)
        .where(KnowledgeDocumentVersion.document_id == document.id)
        .order_by(KnowledgeDocumentVersion.version_number.desc())
    )
    steps = (
        list(
            db.scalars(
                select(ProcessingStep)
                .where(
                    ProcessingStep.subject_type == "knowledge_version",
                    ProcessingStep.subject_id == latest.id,
                )
                .order_by(ProcessingStep.created_at)
            )
        )
        if latest
        else []
    )
    return KnowledgeOut.model_validate(document).model_copy(
        update={"latest_version_status": latest.status if latest else None, "steps": steps}
    )


@router.get("", response_model=list[KnowledgeOut])
def list_knowledge(
    db: Session = Depends(get_db), _: User = Depends(current_user)
) -> list[KnowledgeOut]:
    documents = db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.status == "active")
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return [knowledge_out(db, document) for document in documents]


@router.post("", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED)
async def upload_knowledge(
    title: str = Form(...),
    category: KnowledgeCategory = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> KnowledgeOut:
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "知识文档为空")
    suffix = (file.filename or "").lower()
    if not suffix.endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "仅支持 PDF、DOCX、TXT")
    document = KnowledgeDocument(title=title, category=category, created_by=user.id)
    db.add(document)
    db.flush()
    key = f"knowledge/{document.id}/v1/{file.filename}"
    get_storage().put_bytes(key, data, file.content_type or "application/octet-stream")
    version = KnowledgeDocumentVersion(
        document_id=document.id,
        version_number=1,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        object_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(version)
    db.commit()
    dispatch_knowledge(version.id)
    db.refresh(document)
    return knowledge_out(db, document)


@router.post("/search", response_model=SearchOut)
def query_knowledge(
    payload: SearchIn,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
) -> dict:
    return search_knowledge(db, payload.query, payload.limit)


@router.post("/{document_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_knowledge(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    version = db.scalar(
        select(KnowledgeDocumentVersion)
        .where(KnowledgeDocumentVersion.document_id == document_id)
        .order_by(KnowledgeDocumentVersion.version_number.desc())
    )
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识文档不存在")
    version.status = ProcessingStatus.PENDING
    version.error_message = None
    db.commit()
    dispatch_knowledge(version.id)
    return {"status": "pending"}


@router.get("/{document_id}/source")
def knowledge_source(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    document = db.get(KnowledgeDocument, document_id)
    if not document or document.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识文档不存在")
    version = db.scalar(
        select(KnowledgeDocumentVersion)
        .where(KnowledgeDocumentVersion.document_id == document.id)
        .order_by(KnowledgeDocumentVersion.version_number.desc())
    )
    if not version:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识文档文件不存在")
    url = get_storage().presign_get(version.object_key)
    if url:
        return RedirectResponse(url)
    return Response(get_storage().read_bytes(version.object_key), media_type=version.content_type)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    document = db.get(KnowledgeDocument, document_id)
    if not document or document.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识文档不存在")
    document.status = "deleted"
    from ..models import now_utc

    document.deleted_at = now_utc()
    db.commit()
