from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import accessible_inspection, get_current_user, model_dict
from app.core.database import get_db
from app.models import Evidence, Fact, FactEvidence, User
from app.services.audit import audit
from app.services.documents import (
    mark_documents_stale,
    mark_fact_dependent_documents_stale,
    refresh_unedited_questions_answers,
)

router = APIRouter(tags=["检查事实"])


class FactCreate(BaseModel):
    kind: str = Field(default="observation", max_length=40)
    summary: str = Field(min_length=1)
    content: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class FactUpdate(BaseModel):
    kind: str | None = Field(default=None, max_length=40)
    summary: str | None = Field(default=None, min_length=1)
    content: dict[str, Any] | None = None
    evidence_ids: list[str] | None = None


def serialize_fact(db: Session, item: Fact) -> dict:
    evidence_ids = db.scalars(
        select(FactEvidence.evidence_id).where(FactEvidence.fact_id == item.id)
    ).all()
    data = model_dict(
        item,
        "id",
        "inspection_id",
        "kind",
        "summary",
        "content",
        "status",
        "source",
        "confidence",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    )
    data["evidence_ids"] = list(evidence_ids)
    return data


def _replace_evidence(db: Session, fact: Fact, evidence_ids: list[str]) -> None:
    if evidence_ids:
        valid = set(
            db.scalars(
                select(Evidence.id).where(
                    Evidence.inspection_id == fact.inspection_id, Evidence.id.in_(evidence_ids)
                )
            ).all()
        )
        if valid != set(evidence_ids):
            raise HTTPException(422, "存在不属于当前检查的证据")
    db.execute(delete(FactEvidence).where(FactEvidence.fact_id == fact.id))
    for evidence_id in evidence_ids:
        db.add(FactEvidence(fact_id=fact.id, evidence_id=evidence_id))


@router.get("/inspections/{inspection_id}/facts")
def list_facts(
    inspection_id: str,
    include_rejected: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accessible_inspection(db, user, inspection_id)
    query = select(Fact).where(Fact.inspection_id == inspection_id).order_by(Fact.created_at)
    if not include_rejected:
        query = query.where(Fact.status != "rejected")
    return [serialize_fact(db, item) for item in db.scalars(query).all()]


@router.post("/inspections/{inspection_id}/facts", status_code=201)
def create_fact(
    inspection_id: str,
    payload: FactCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    accessible_inspection(db, user, inspection_id)
    fact = Fact(
        inspection_id=inspection_id,
        kind=payload.kind,
        summary=payload.summary,
        content=payload.content,
        status="confirmed",
        source="user",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(fact)
    db.flush()
    _replace_evidence(db, fact, payload.evidence_ids)
    mark_fact_dependent_documents_stale(db, inspection_id)
    if fact.kind in {"qa", "statement"}:
        refresh_unedited_questions_answers(db, inspection_id)
    audit(
        db,
        actor_id=user.id,
        action="fact.created",
        target_type="fact",
        target_id=fact.id,
        inspection_id=inspection_id,
    )
    db.commit()
    return serialize_fact(db, fact)


def _owned_fact(db: Session, user: User, fact_id: str) -> Fact:
    fact = db.get(Fact, fact_id)
    if not fact:
        raise HTTPException(404, "事实不存在")
    accessible_inspection(db, user, fact.inspection_id)
    return fact


@router.patch("/facts/{fact_id}")
def update_fact(
    fact_id: str,
    payload: FactUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    fact = _owned_fact(db, user, fact_id)
    previous_kind = fact.kind
    if payload.kind is not None:
        fact.kind = payload.kind
    if payload.summary is not None:
        fact.summary = payload.summary
    if payload.content is not None:
        fact.content = payload.content
    if payload.evidence_ids is not None:
        _replace_evidence(db, fact, payload.evidence_ids)
    fact.updated_by = user.id
    if fact.status == "confirmed":
        mark_fact_dependent_documents_stale(db, fact.inspection_id)
        if {previous_kind, fact.kind}.intersection({"qa", "statement"}):
            refresh_unedited_questions_answers(db, fact.inspection_id)
    else:
        mark_documents_stale(db, fact.id)
    audit(
        db,
        actor_id=user.id,
        action="fact.updated",
        target_type="fact",
        target_id=fact.id,
        inspection_id=fact.inspection_id,
    )
    db.commit()
    return serialize_fact(db, fact)


@router.post("/facts/{fact_id}/confirm")
def confirm_fact(
    fact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    fact = _owned_fact(db, user, fact_id)
    if fact.source == "ai" and not db.scalar(
        select(FactEvidence).where(FactEvidence.fact_id == fact.id)
    ):
        raise HTTPException(422, "AI 候选事实缺少证据，不能确认")
    was_confirmed = fact.status == "confirmed"
    fact.status = "confirmed"
    fact.updated_by = user.id
    if was_confirmed:
        mark_documents_stale(db, fact.id)
    else:
        mark_fact_dependent_documents_stale(db, fact.inspection_id)
    if fact.kind in {"qa", "statement"}:
        refresh_unedited_questions_answers(db, fact.inspection_id)
    audit(
        db,
        actor_id=user.id,
        action="fact.confirmed",
        target_type="fact",
        target_id=fact.id,
        inspection_id=fact.inspection_id,
    )
    db.commit()
    return serialize_fact(db, fact)


@router.post("/facts/{fact_id}/reject")
def reject_fact(
    fact_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    fact = _owned_fact(db, user, fact_id)
    was_confirmed = fact.status == "confirmed"
    fact.status = "rejected"
    fact.updated_by = user.id
    if was_confirmed:
        mark_fact_dependent_documents_stale(db, fact.inspection_id)
    else:
        mark_documents_stale(db, fact.id)
    if fact.kind in {"qa", "statement"}:
        refresh_unedited_questions_answers(db, fact.inspection_id)
    audit(
        db,
        actor_id=user.id,
        action="fact.rejected",
        target_type="fact",
        target_id=fact.id,
        inspection_id=fact.inspection_id,
    )
    db.commit()
    return serialize_fact(db, fact)
