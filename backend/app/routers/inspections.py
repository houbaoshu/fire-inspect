import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..database import get_db
from ..document_service import source_value, synchronize_shared_document_fields
from ..models import Inspection, InspectionMember, User, UserRole
from ..schemas import InspectionCreate, InspectionOut, InspectionUpdate, MemberUpdate
from ..security import accessible_inspections, current_user, require_inspection

router = APIRouter(prefix="/inspections", tags=["inspections"])


@router.get("", response_model=list[InspectionOut])
def list_inspections(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[Inspection]:
    return list(db.scalars(accessible_inspections(user).order_by(Inspection.updated_at.desc())))


@router.post("", response_model=InspectionOut, status_code=status.HTTP_201_CREATED)
def create_inspection(
    payload: InspectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Inspection:
    inspection = Inspection(owner_id=user.id, **payload.model_dump())
    db.add(inspection)
    db.flush()
    record_audit(
        db,
        user,
        "inspection.created",
        "inspection",
        inspection.id,
        after=payload.model_dump(mode="json"),
    )
    db.commit()
    return inspection


@router.get("/{inspection_id}", response_model=InspectionOut)
def get_inspection(
    inspection_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Inspection:
    return require_inspection(db, user, inspection_id)


@router.patch("/{inspection_id}", response_model=InspectionOut)
def update_inspection(
    inspection_id: uuid.UUID,
    payload: InspectionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> Inspection:
    inspection = require_inspection(db, user, inspection_id)
    if inspection.row_version != payload.row_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "检查已被其他用户修改，请刷新后重试")
    before = {
        "title": inspection.title,
        "unit_name": inspection.unit_name,
        "address": inspection.address,
        "inspected_at": inspection.inspected_at.isoformat() if inspection.inspected_at else None,
        "dynamic_attributes": dict(inspection.dynamic_attributes or {}),
        "status": inspection.status.value,
        "row_version": inspection.row_version,
    }
    updates = payload.model_dump(exclude={"row_version"}, exclude_unset=True)
    shared_paths: set[str] = set()
    for key in ("unit_name", "address", "inspected_at"):
        if key in updates and getattr(inspection, key) != updates[key]:
            shared_paths.add(f"inspection.{key}")
    if "dynamic_attributes" in updates and updates["dynamic_attributes"] is not None:
        old_attributes = dict(inspection.dynamic_attributes or {})
        new_attributes = dict(updates["dynamic_attributes"])
        shared_paths.update(
            f"custom.{key}"
            for key in old_attributes.keys() | new_attributes.keys()
            if old_attributes.get(key) != new_attributes.get(key) and key != "photo_description"
        )
    for key, value in updates.items():
        setattr(inspection, key, value)
    inspection.row_version += 1
    affected_documents: set[uuid.UUID] = set()
    for source_path in shared_paths:
        affected_documents.update(
            synchronize_shared_document_fields(
                db,
                inspection,
                source_path,
                source_value(db, inspection, source_path),
                human_edit=False,
            )
        )
    record_audit(
        db,
        user,
        "inspection.updated",
        "inspection",
        inspection.id,
        before=before,
        after={
            **payload.model_dump(mode="json", exclude_unset=True),
            "affected_documents": [str(item) for item in sorted(affected_documents, key=str)],
        },
    )
    db.commit()
    return inspection


@router.put("/{inspection_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def update_members(
    inspection_id: uuid.UUID,
    payload: MemberUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    inspection = require_inspection(db, user, inspection_id)
    if user.role != UserRole.ADMIN and inspection.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有检查创建者或管理员可以分配成员")
    valid_ids = set(
        db.scalars(select(User.id).where(User.id.in_(payload.user_ids), User.is_active))
    )
    if valid_ids != set(payload.user_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "包含不存在或不可用的用户")
    db.execute(delete(InspectionMember).where(InspectionMember.inspection_id == inspection.id))
    for user_id in valid_ids:
        if user_id != inspection.owner_id:
            db.add(InspectionMember(inspection_id=inspection.id, user_id=user_id))
    record_audit(
        db,
        user,
        "inspection.members_updated",
        "inspection",
        inspection.id,
        after={"user_ids": [str(item) for item in valid_ids]},
    )
    db.commit()
