from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from django.db import transaction

from fire_inspect.audit.models import (
    AuditEvent,
    DomainChangeHistory,
    masked_change_summary,
)
from fire_inspect.inspections.models import Inspection, InspectionPersonnel
from fire_inspect.inspections.signals import (
    inspection_deleted,
    inspection_shared_fields_changed,
)

SHARED_FIELDS = (
    "unit_name",
    "unit_address",
    "responsible_person",
    "contact_name",
    "contact_phone",
    "inspection_at",
    "inspection_type",
    "general_notes",
)


class RevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"current revision is {current_revision}")


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _personnel_snapshot(inspection: Inspection) -> list[dict[str, Any]]:
    return [
        {
            "user_id": str(item.user_id) if item.user_id else None,
            "name": item.name,
            "work_unit": item.work_unit,
            "role": item.role,
        }
        for item in inspection.personnel.order_by("sequence")
    ]


def _replace_personnel(inspection: Inspection, personnel_values: list[dict[str, Any]]) -> None:
    inspection.personnel.all().delete()
    InspectionPersonnel.objects.bulk_create(
        [
            InspectionPersonnel(inspection=inspection, sequence=sequence, **values)
            for sequence, values in enumerate(personnel_values)
        ]
    )


@transaction.atomic
def create_inspection(*, actor, values, personnel_values) -> Inspection:
    inspection = Inspection.objects.create(owner=actor, **values)
    _replace_personnel(inspection, personnel_values)
    DomainChangeHistory.objects.create(
        inspection=inspection,
        actor=actor,
        object_type="inspection",
        object_id=inspection.id,
        action="created",
        old_values={},
        new_values={"shared_revision": 1},
    )
    AuditEvent.append(
        actor=actor,
        object_type="inspection",
        object_id=inspection.id,
        inspection_id_snapshot=inspection.id,
        action="created",
        masked_change_summary="inspection created",
    )
    return inspection


@transaction.atomic
def update_inspection(
    *, inspection_id, actor, expected_revision: int, values: dict[str, Any]
) -> Inspection:
    inspection = Inspection.objects.select_for_update().get(pk=inspection_id)
    if expected_revision != inspection.shared_revision:
        raise RevisionConflict(inspection.shared_revision)

    personnel_marker = object()
    personnel_values = values.pop("personnel", personnel_marker)
    old_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}
    changed_fields: list[str] = []
    for field in SHARED_FIELDS:
        if field not in values:
            continue
        old = getattr(inspection, field)
        new = values[field]
        if old != new:
            old_values[field] = _json_value(old)
            new_values[field] = _json_value(new)
            changed_fields.append(field)
            setattr(inspection, field, new)

    if personnel_values is not personnel_marker:
        old_personnel = _personnel_snapshot(inspection)
        normalized_personnel = [_json_value(item) for item in personnel_values]
        if old_personnel != normalized_personnel:
            old_values["personnel"] = old_personnel
            new_values["personnel"] = normalized_personnel
            changed_fields.append("personnel")
            _replace_personnel(inspection, personnel_values)

    if not changed_fields:
        return inspection

    inspection.shared_revision += 1
    inspection.save(
        update_fields=(
            *[f for f in changed_fields if f != "personnel"],
            "shared_revision",
            "updated_at",
        )
    )
    DomainChangeHistory.objects.create(
        inspection=inspection,
        actor=actor,
        object_type="inspection",
        object_id=inspection.id,
        action="updated",
        old_values=old_values,
        new_values=new_values,
    )
    AuditEvent.append(
        actor=actor,
        object_type="inspection",
        object_id=inspection.id,
        inspection_id_snapshot=inspection.id,
        action="updated",
        masked_change_summary=masked_change_summary(
            {
                field: {"old": old_values[field], "new": new_values[field]}
                for field in changed_fields
            }
        ),
    )
    inspection_shared_fields_changed.send(
        sender=Inspection,
        inspection=inspection,
        actor=actor,
        changed_fields=tuple(changed_fields),
        old_values=old_values,
        new_values=new_values,
        reason_code="SHARED_FIELDS_CHANGED",
    )
    return inspection


def delete_inspection(*, inspection: Inspection, actor, reason: str) -> UUID:
    inspection_id = inspection.id
    from fire_inspect.materials.services import (
        cleanup_quarantined_files,
        quarantine_material_files,
        restore_quarantined_files,
    )

    quarantined = []
    try:
        with transaction.atomic():
            inspection = Inspection.objects.select_for_update().get(pk=inspection_id)
            quarantined = quarantine_material_files(inspection.materials.all())
            inspection_deleted.send(
                sender=Inspection,
                inspection=inspection,
                actor=actor,
                reason=reason,
            )
            AuditEvent.append(
                actor=actor,
                object_type="inspection",
                object_id=inspection_id,
                inspection_id_snapshot=inspection_id,
                action="deleted",
                reason=reason,
                masked_change_summary="inspection deleted",
            )
            inspection.delete()
    except Exception:
        restore_quarantined_files(quarantined)
        raise
    cleanup_quarantined_files(quarantined)
    return inspection_id
