from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_inspection, get_current_user, model_dict
from app.core.database import get_db
from app.models import AuditEvent, DocumentDraft, Inspection, User
from app.services.audit import audit

router = APIRouter(prefix="/inspections", tags=["检查"])


class InspectionInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class InspectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    extra_fields: dict[str, Any] | None = None


def serialize(item: Inspection) -> dict:
    return model_dict(
        item, "id", "owner_id", "title", "status", "extra_fields", "created_at", "updated_at"
    )


@router.get("")
def list_inspections(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = select(Inspection).order_by(Inspection.updated_at.desc())
    if user.role != "admin":
        query = query.where(Inspection.owner_id == user.id)
    return [serialize(item) for item in db.scalars(query).all()]


@router.post("", status_code=201)
def create_inspection(
    payload: InspectionInput, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    inspection = Inspection(
        owner_id=user.id, title=payload.title, extra_fields=payload.extra_fields
    )
    db.add(inspection)
    db.flush()
    audit(
        db,
        actor_id=user.id,
        action="inspection.created",
        target_type="inspection",
        target_id=inspection.id,
        inspection_id=inspection.id,
    )
    db.commit()
    return serialize(inspection)


@router.get("/{inspection_id}")
def get_inspection(
    inspection_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return serialize(accessible_inspection(db, user, inspection_id))


@router.patch("/{inspection_id}")
def update_inspection(
    inspection_id: str,
    payload: InspectionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    inspection = accessible_inspection(db, user, inspection_id)
    changed_fields: set[str] = set()
    if payload.title is not None:
        if payload.title != inspection.title:
            changed_fields.add("inspection_title")
        inspection.title = payload.title
    if payload.extra_fields is not None:
        changed_fields.update(
            key
            for key, value in payload.extra_fields.items()
            if inspection.extra_fields.get(key) != value
        )
        inspection.extra_fields = payload.extra_fields
    for draft in db.scalars(
        select(DocumentDraft).where(DocumentDraft.inspection_id == inspection.id)
    ).all():
        fields = dict(draft.fields_json)
        dirty = False
        for code in changed_fields:
            if code in fields:
                state = dict(fields[code])
                state["stale"] = True
                fields[code] = state
                dirty = True
        if dirty:
            draft.fields_json = fields
            draft.status = "draft"
            draft.confirmed_by = None
            draft.confirmed_at = None
    audit(
        db,
        actor_id=user.id,
        action="inspection.updated",
        target_type="inspection",
        target_id=inspection.id,
        inspection_id=inspection.id,
    )
    db.commit()
    return serialize(inspection)


@router.get("/{inspection_id}/audit-events")
def list_audit_events(
    inspection_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    accessible_inspection(db, user, inspection_id)
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.inspection_id == inspection_id)
        .order_by(AuditEvent.created_at.desc())
    ).all()
    return [
        model_dict(
            item, "id", "actor_id", "action", "target_type", "target_id", "details", "created_at"
        )
        for item in events
    ]
