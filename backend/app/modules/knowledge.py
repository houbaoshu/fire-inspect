from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_inspection, get_current_user, model_dict, require_admin
from app.core.database import get_db
from app.core.storage import LocalStorage, get_storage
from app.models import Citation, Fact, KnowledgeDocument, LegalAnalysis, User, new_id
from app.services.audit import audit
from app.services.jobs import create_job
from app.services.knowledge import query_knowledge

router = APIRouter(tags=["知识库"])


def serialize_document(item: KnowledgeDocument) -> dict:
    return model_dict(
        item,
        "id",
        "title",
        "filename",
        "content_type",
        "material_type",
        "enabled",
        "indexing_status",
        "indexing_error",
        "uploaded_by",
        "created_at",
        "updated_at",
    )


@router.get("/knowledge-documents")
def list_documents(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [
        serialize_document(item)
        for item in db.scalars(
            select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc())
        ).all()
    ]


@router.post("/knowledge-documents", status_code=201)
def upload_document(
    title: str = Form(...),
    material_type: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    filename = Path(file.filename or "document").name
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        raise HTTPException(415, "仅支持 PDF、DOCX 和 TXT")
    if material_type not in {"formal", "reference"}:
        raise HTTPException(422, "材料性质只能是 formal 或 reference")
    document_id = new_id()
    key = f"knowledge/{document_id}{suffix}"
    get_storage().put_fileobj(key, file.file, file.content_type)
    document = KnowledgeDocument(
        id=document_id,
        title=title,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        material_type=material_type,
        storage_key=key,
        uploaded_by=user.id,
    )
    db.add(document)
    db.commit()
    job = create_job(
        db, kind="index_knowledge", target_type="knowledge_document", target_id=document.id
    )
    return {"document": serialize_document(document), "job_id": job.id}


@router.patch("/knowledge-documents/{document_id}")
def set_document_enabled(
    document_id: str,
    enabled: bool,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "知识文档不存在")
    document.enabled = enabled
    db.commit()
    return serialize_document(document)


@router.post("/knowledge-documents/{document_id}/retry")
def retry_index(document_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "知识文档不存在")
    document.indexing_status = "pending"
    document.indexing_error = None
    db.commit()
    job = create_job(
        db, kind="index_knowledge", target_type="knowledge_document", target_id=document.id
    )
    return {"job_id": job.id}


@router.get("/knowledge-documents/{document_id}/content")
def document_content(
    document_id: str, _: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "知识文档不存在")
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        return FileResponse(
            storage.local_path(document.storage_key),
            media_type=document.content_type,
            filename=document.filename,
        )
    return RedirectResponse(storage.presigned_url(document.storage_key, document.filename))


class QueryInput(BaseModel):
    question: str = Field(min_length=2)
    inspection_id: str | None = None
    fact_id: str | None = None


@router.post("/knowledge/query")
def query(
    payload: QueryInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if payload.inspection_id:
        accessible_inspection(db, user, payload.inspection_id)
    if payload.fact_id:
        fact = db.get(Fact, payload.fact_id)
        if not fact or (payload.inspection_id and fact.inspection_id != payload.inspection_id):
            raise HTTPException(422, "事实不属于当前检查")
        accessible_inspection(db, user, fact.inspection_id)
        if fact.status != "confirmed":
            raise HTTPException(422, "只能对已确认事实进行法规分析")
    analysis, citations = query_knowledge(
        db,
        question=payload.question,
        user_id=user.id,
        inspection_id=payload.inspection_id,
        fact_id=payload.fact_id,
    )
    if payload.inspection_id:
        audit(
            db,
            actor_id=user.id,
            action="knowledge.queried",
            target_type="legal_analysis",
            target_id=analysis.id,
            inspection_id=payload.inspection_id,
        )
        db.commit()
    return {
        **model_dict(
            analysis,
            "id",
            "inspection_id",
            "fact_id",
            "question",
            "characterization",
            "reasoning",
            "created_at",
        ),
        "citations": [
            {
                **model_dict(item, "id", "document_id", "chunk_id", "excerpt", "locator"),
                "document_title": db.get(KnowledgeDocument, item.document_id).title,
                "material_type": db.get(KnowledgeDocument, item.document_id).material_type,
            }
            for item in citations
        ],
    }


@router.get("/inspections/{inspection_id}/legal-analyses")
def list_analyses(
    inspection_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    accessible_inspection(db, user, inspection_id)
    analyses = db.scalars(
        select(LegalAnalysis)
        .where(LegalAnalysis.inspection_id == inspection_id)
        .order_by(LegalAnalysis.created_at.desc())
    ).all()
    output = []
    for analysis in analyses:
        citations = db.scalars(select(Citation).where(Citation.analysis_id == analysis.id)).all()
        output.append(
            {
                **model_dict(
                    analysis,
                    "id",
                    "fact_id",
                    "question",
                    "characterization",
                    "reasoning",
                    "created_at",
                ),
                "citations": [
                    {
                        **model_dict(item, "id", "document_id", "chunk_id", "excerpt", "locator"),
                        "document_title": db.get(KnowledgeDocument, item.document_id).title,
                        "material_type": db.get(KnowledgeDocument, item.document_id).material_type,
                    }
                    for item in citations
                ],
            }
        )
    return output
