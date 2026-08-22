import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..database import get_db
from ..document_service import mark_document_fields_stale
from ..models import (
    Evidence,
    Fact,
    FactEvidenceLink,
    FactStatus,
    Material,
    User,
    ValueSource,
)
from ..schemas import EvidenceCreate, EvidenceOut, FactCreate, FactOut, FactUpdate
from ..security import current_user, require_inspection

router = APIRouter(tags=["facts"])


def fact_out(db: Session, fact: Fact) -> FactOut:
    evidence = list(
        db.scalars(
            select(Evidence)
            .join(FactEvidenceLink, FactEvidenceLink.evidence_id == Evidence.id)
            .where(FactEvidenceLink.fact_id == fact.id)
            .order_by(Evidence.created_at)
        )
    )
    return FactOut.model_validate(fact).model_copy(update={"evidence": evidence})


def _get_fact(db: Session, user: User, fact_id: uuid.UUID) -> Fact:
    fact = db.get(Fact, fact_id)
    if not fact or fact.status == FactStatus.DELETED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "检查事实不存在")
    require_inspection(db, user, fact.inspection_id)
    return fact


def _validate_evidence(
    db: Session, inspection_id: uuid.UUID, evidence_ids: list[uuid.UUID]
) -> list[Evidence]:
    if not evidence_ids:
        return []
    rows = list(
        db.scalars(
            select(Evidence).where(
                Evidence.id.in_(evidence_ids), Evidence.inspection_id == inspection_id
            )
        )
    )
    if len(rows) != len(set(evidence_ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "包含不属于当前检查的证据")
    return rows


@router.get("/inspections/{inspection_id}/facts", response_model=list[FactOut])
def list_facts(
    inspection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[FactOut]:
    require_inspection(db, user, inspection_id)
    facts = db.scalars(
        select(Fact)
        .where(Fact.inspection_id == inspection_id, Fact.status != FactStatus.DELETED)
        .order_by(Fact.status, Fact.created_at)
    )
    return [fact_out(db, fact) for fact in facts]


@router.post("/inspections/{inspection_id}/facts", response_model=FactOut)
def create_fact(
    inspection_id: uuid.UUID,
    payload: FactCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FactOut:
    require_inspection(db, user, inspection_id)
    evidence = _validate_evidence(db, inspection_id, payload.evidence_ids)
    fact = Fact(
        inspection_id=inspection_id,
        title=payload.title,
        detail=payload.detail,
        status=FactStatus.CANDIDATE,
        source=ValueSource.HUMAN,
        needs_confirmation=True,
        manually_edited=True,
    )
    db.add(fact)
    db.flush()
    for item in evidence:
        db.add(FactEvidenceLink(fact_id=fact.id, evidence_id=item.id))
    record_audit(db, user, "fact.created", "fact", fact.id, after=payload.model_dump(mode="json"))
    db.commit()
    return fact_out(db, fact)


@router.patch("/facts/{fact_id}", response_model=FactOut)
def update_fact(
    fact_id: uuid.UUID,
    payload: FactUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FactOut:
    fact = _get_fact(db, user, fact_id)
    if fact.row_version != payload.row_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "检查事实已被其他用户修改")
    before = {"title": fact.title, "detail": fact.detail, "row_version": fact.row_version}
    if payload.evidence_ids is not None:
        evidence = _validate_evidence(db, fact.inspection_id, payload.evidence_ids)
        db.execute(delete(FactEvidenceLink).where(FactEvidenceLink.fact_id == fact.id))
        for item in evidence:
            db.add(FactEvidenceLink(fact_id=fact.id, evidence_id=item.id))
    if payload.title is not None:
        fact.title = payload.title
    if payload.detail is not None:
        fact.detail = payload.detail
    fact.source = ValueSource.HUMAN
    fact.manually_edited = True
    fact.row_version += 1
    if fact.status == FactStatus.CONFIRMED:
        mark_document_fields_stale(db, fact.inspection_id, "facts.")
    record_audit(
        db,
        user,
        "fact.updated",
        "fact",
        fact.id,
        before=before,
        after=payload.model_dump(mode="json", exclude_unset=True),
    )
    db.commit()
    return fact_out(db, fact)


@router.post("/facts/{fact_id}/confirm", response_model=FactOut)
def confirm_fact(
    fact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FactOut:
    fact = _get_fact(db, user, fact_id)
    before = {"status": fact.status.value}
    fact.status = FactStatus.CONFIRMED
    fact.needs_confirmation = False
    fact.confirmed_by = user.id
    fact.confirmed_at = datetime.now(UTC)
    fact.row_version += 1
    mark_document_fields_stale(db, fact.inspection_id, "facts.")
    record_audit(db, user, "fact.confirmed", "fact", fact.id, before, {"status": "confirmed"})
    db.commit()
    return fact_out(db, fact)


@router.post("/facts/{fact_id}/unconfirm", response_model=FactOut)
def unconfirm_fact(
    fact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> FactOut:
    fact = _get_fact(db, user, fact_id)
    fact.status = FactStatus.CANDIDATE
    fact.needs_confirmation = True
    fact.confirmed_by = None
    fact.confirmed_at = None
    fact.row_version += 1
    mark_document_fields_stale(db, fact.inspection_id, "facts.")
    record_audit(db, user, "fact.unconfirmed", "fact", fact.id)
    db.commit()
    return fact_out(db, fact)


@router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fact(
    fact_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    fact = _get_fact(db, user, fact_id)
    fact.status = FactStatus.DELETED
    fact.row_version += 1
    mark_document_fields_stale(db, fact.inspection_id, "facts.")
    record_audit(db, user, "fact.deleted", "fact", fact.id)
    db.commit()


@router.get("/inspections/{inspection_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(
    inspection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> list[Evidence]:
    require_inspection(db, user, inspection_id)
    return list(
        db.scalars(
            select(Evidence)
            .where(Evidence.inspection_id == inspection_id)
            .order_by(Evidence.created_at.desc())
        )
    )


@router.post("/inspections/{inspection_id}/evidence", response_model=EvidenceOut)
def create_evidence(
    inspection_id: uuid.UUID,
    payload: EvidenceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Evidence:
    require_inspection(db, user, inspection_id)
    if payload.material_id:
        material = db.get(Material, payload.material_id)
        if not material or material.inspection_id != inspection_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "材料不属于当前检查")
    evidence = Evidence(inspection_id=inspection_id, created_by=user.id, **payload.model_dump())
    db.add(evidence)
    db.commit()
    return evidence


@router.delete("/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(
    evidence_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    evidence = db.get(Evidence, evidence_id)
    if not evidence:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "证据不存在")
    require_inspection(db, user, evidence.inspection_id)
    db.execute(delete(FactEvidenceLink).where(FactEvidenceLink.evidence_id == evidence.id))
    db.delete(evidence)
    record_audit(db, user, "evidence.deleted", "evidence", evidence.id)
    db.commit()
