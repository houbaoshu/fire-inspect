from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework import status

from fire_inspect.audit.models import AuditEvent, DomainChangeHistory
from fire_inspect.inspections.models import Inspection
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def test_inspection_changes_write_detailed_history_and_hash_chained_audit(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    response = authenticated_client(inspector_a).patch(
        f"/api/v1/inspections/{inspection.id}",
        {"contact_phone": "13900000000", "shared_revision": 1},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK

    history = DomainChangeHistory.objects.get(
        object_type="inspection", object_id=inspection.id, action="updated"
    )
    assert history.old_values == {"contact_phone": "13800000000"}
    assert history.new_values == {"contact_phone": "13900000000"}

    event = AuditEvent.objects.get(
        object_type="inspection", object_id=inspection.id, action="updated"
    )
    assert "13900000000" not in event.masked_change_summary
    assert event.event_hash


def test_audit_event_is_append_only(inspector_a) -> None:
    event = AuditEvent.append(
        actor=inspector_a,
        object_type="user",
        object_id=inspector_a.id,
        action="viewed",
        masked_change_summary="user viewed",
    )
    event.action = "tampered"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()


def test_audit_hash_chain_can_be_verified(inspector_a, capsys) -> None:
    first = AuditEvent.append(
        actor=inspector_a,
        object_type="user",
        object_id=inspector_a.id,
        action="created",
        masked_change_summary="user created",
    )
    second = AuditEvent.append(
        actor=inspector_a,
        object_type="user",
        object_id=inspector_a.id,
        action="updated",
        masked_change_summary="user updated",
    )
    assert second.previous_hash == first.event_hash
    call_command("verify_audit_chain")
    assert "审计链校验通过" in capsys.readouterr().out


def test_admin_audit_endpoint_and_inspector_forbidden(
    authenticated_client, admin_user, inspector_a
) -> None:
    AuditEvent.append(
        actor=inspector_a,
        object_type="inspection",
        object_id=None,
        action="listed",
        masked_change_summary="inspection list",
    )
    forbidden = authenticated_client(inspector_a).get("/api/v1/admin/audit")
    assert forbidden.status_code == status.HTTP_403_FORBIDDEN

    response = authenticated_client(admin_user).get("/api/v1/admin/audit")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["action"] == "listed"
    assert "old_values" not in response.json()[0]


def test_inspection_delete_keeps_minimized_audit(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    client = authenticated_client(inspector_a)

    preview = client.get(f"/api/v1/inspections/{inspection.id}/deletion-impact")
    assert preview.status_code == status.HTTP_200_OK
    assert preview.json()["requires_confirmation"] is True

    refused = client.delete(
        f"/api/v1/inspections/{inspection.id}",
        {"confirm": False, "reason": "重复录入"},
        format="json",
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST

    deleted = client.delete(
        f"/api/v1/inspections/{inspection.id}",
        {"confirm": True, "reason": "重复录入"},
        format="json",
    )
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert not Inspection.objects.filter(id=inspection.id).exists()
    assert not DomainChangeHistory.objects.filter(inspection_id=inspection.id).exists()
    event = AuditEvent.objects.get(
        object_type="inspection", object_id=inspection.id, action="deleted"
    )
    assert event.reason == "重复录入"
