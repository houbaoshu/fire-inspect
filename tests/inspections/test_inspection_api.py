from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone
from rest_framework import status

from fire_inspect.inspections.models import Inspection

pytestmark = pytest.mark.django_db


def inspection_payload(**overrides):
    payload = {
        "unit_name": "某商业综合体",
        "unit_address": "桐乡市测试路 1 号",
        "responsible_person": "负责人",
        "contact_name": "联系人",
        "contact_phone": "13800000000",
        "inspection_at": "2026-08-24T09:30:00+08:00",
        "inspection_type": "other",
        "general_notes": "例行检查",
        "personnel": [{"name": "检查员甲", "work_unit": "消防救援大队", "role": "inspector"}],
    }
    payload.update(overrides)
    return payload


def create_inspection(owner, **overrides) -> Inspection:
    values = inspection_payload(**overrides)
    values.pop("personnel")
    values["inspection_at"] = datetime.fromisoformat(values["inspection_at"])
    return Inspection.objects.create(owner=owner, **values)


def test_owner_create_list_retrieve_and_admin_visibility(
    authenticated_client, inspector_a, inspector_b, admin_user
) -> None:
    created = authenticated_client(inspector_a).post(
        "/api/v1/inspections", inspection_payload(), format="json"
    )
    assert created.status_code == status.HTTP_201_CREATED
    body = created.json()
    assert body["owner"]["id"] == str(inspector_a.id)
    assert body["shared_revision"] == 1
    assert body["personnel"][0]["name"] == "检查员甲"

    assert len(authenticated_client(inspector_a).get("/api/v1/inspections").json()) == 1
    assert authenticated_client(inspector_b).get("/api/v1/inspections").json() == []
    assert (
        authenticated_client(inspector_b).get(f"/api/v1/inspections/{body['id']}").status_code
        == status.HTTP_404_NOT_FOUND
    )
    admin_list = authenticated_client(admin_user).get("/api/v1/inspections")
    assert [item["id"] for item in admin_list.json()] == [body["id"]]


@pytest.mark.parametrize("role", ["inspector", "admin"])
def test_all_roles_can_create_inspection(authenticated_client, user_factory, role) -> None:
    user = user_factory(f"creator-{role}", role=role)
    response = authenticated_client(user).post(
        "/api/v1/inspections", inspection_payload(), format="json"
    )
    assert response.status_code == status.HTTP_201_CREATED


def test_shared_update_requires_revision_and_rejects_stale_write(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    client = authenticated_client(inspector_a)

    missing = client.patch(
        f"/api/v1/inspections/{inspection.id}", {"unit_address": "新地址"}, format="json"
    )
    assert missing.status_code == status.HTTP_428_PRECONDITION_REQUIRED

    stale = client.patch(
        f"/api/v1/inspections/{inspection.id}",
        {"unit_address": "新地址", "shared_revision": 999},
        format="json",
    )
    assert stale.status_code == status.HTTP_409_CONFLICT
    assert stale.json()["error"]["code"] == "revision_conflict"

    updated = client.patch(
        f"/api/v1/inspections/{inspection.id}",
        {"unit_address": "新地址", "shared_revision": 1},
        format="json",
    )
    assert updated.status_code == status.HTTP_200_OK
    assert updated.json()["shared_revision"] == 2
    inspection.refresh_from_db()
    assert inspection.unit_address == "新地址"


def test_if_match_revision_and_noop_update_do_not_bump_revision(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    client = authenticated_client(inspector_a)
    response = client.patch(
        f"/api/v1/inspections/{inspection.id}",
        {"unit_name": inspection.unit_name},
        format="json",
        HTTP_IF_MATCH='"1"',
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["shared_revision"] == 1


def test_shared_change_signal_contains_old_and_new_values(
    authenticated_client, inspector_a
) -> None:
    from fire_inspect.inspections.signals import inspection_shared_fields_changed

    inspection = create_inspection(inspector_a)
    received = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    inspection_shared_fields_changed.connect(receiver, weak=False)
    try:
        response = authenticated_client(inspector_a).patch(
            f"/api/v1/inspections/{inspection.id}",
            {"unit_name": "修改后单位", "shared_revision": 1},
            format="json",
        )
    finally:
        inspection_shared_fields_changed.disconnect(receiver)

    assert response.status_code == status.HTTP_200_OK
    assert received[0]["changed_fields"] == ("unit_name",)
    assert received[0]["old_values"] == {"unit_name": "某商业综合体"}
    assert received[0]["new_values"] == {"unit_name": "修改后单位"}
    assert received[0]["reason_code"] == "SHARED_FIELDS_CHANGED"


def test_invalid_inspection_type_is_rejected(authenticated_client, inspector_a) -> None:
    response = authenticated_client(inspector_a).post(
        "/api/v1/inspections",
        inspection_payload(inspection_type="invented-type"),
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_inspection_time_is_timezone_aware(authenticated_client, inspector_a) -> None:
    response = authenticated_client(inspector_a).post(
        "/api/v1/inspections", inspection_payload(), format="json"
    )
    inspection = Inspection.objects.get(id=response.json()["id"])
    assert timezone.is_aware(inspection.inspection_at)
