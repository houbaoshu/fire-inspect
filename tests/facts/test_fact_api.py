from __future__ import annotations

import pytest
from rest_framework import status

from fire_inspect.ai.schemas import (
    EvidenceReference,
    FactCandidate,
    FactExtractionOutput,
)
from fire_inspect.audit.models import AuditEvent, DomainChangeHistory
from fire_inspect.facts.models import Fact
from fire_inspect.facts.services import publish_ai_fact_candidates
from fire_inspect.materials.services import delete_material
from tests.domain_fixtures import create_media_material
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def fact_list_url(inspection) -> str:
    return f"/api/v1/inspections/{inspection.id}/facts"


def test_manual_fact_lifecycle_and_history(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    client = authenticated_client(inspector_a)
    created = client.post(
        fact_list_url(inspection),
        {
            "content": "现场存在堆放物品。",
            "needs_verification": False,
            "evidence": [{"material_id": str(material.id), "start_ms": 1_000, "end_ms": 3_000}],
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    body = created.json()
    assert body["origin"] == "manual"
    assert body["review_state"] == "candidate"
    assert body["evidence"][0]["material_id"] == str(material.id)

    base = f"{fact_list_url(inspection)}/{body['id']}"
    confirmed = client.post(base + "/confirm", {"revision": 1}, format="json")
    assert confirmed.status_code == status.HTTP_200_OK
    assert confirmed.json()["review_state"] == "confirmed"
    withdrawn = client.post(base + "/withdraw", {"revision": 2}, format="json")
    assert withdrawn.json()["review_state"] == "withdrawn"
    restored = client.post(base + "/restore", {"revision": 3}, format="json")
    assert restored.json()["review_state"] == "candidate"
    assert restored.json()["revision"] == 4

    history = client.get(base + "/history")
    assert [item["action"] for item in history.json()] == [
        "created",
        "confirmed",
        "withdrawn",
        "restored",
    ]


def test_fact_permissions_hide_other_inspectors_data(
    authenticated_client, inspector_a, inspector_b
) -> None:
    inspection = create_inspection(inspector_a)
    created = authenticated_client(inspector_a).post(
        fact_list_url(inspection), {"content": "人工事实"}, format="json"
    )
    other = authenticated_client(inspector_b)
    assert other.get(fact_list_url(inspection)).status_code == status.HTTP_404_NOT_FOUND
    assert (
        other.get(f"{fact_list_url(inspection)}/{created.json()['id']}").status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_ai_candidates_are_idempotent_and_remain_candidates(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    output = FactExtractionOutput(
        (
            FactCandidate(
                "大厅存在堆放物品。",
                (EvidenceReference(material.id, 0, 5_000),),
            ),
            FactCandidate(
                "值班持证情况需要核实。",
                (),
                needs_verification=True,
                verification_note="未见证件信息。",
            ),
        )
    )
    first = publish_ai_fact_candidates(
        inspection=inspection,
        primary_material=material,
        input_revision=1,
        output=output,
        created_by=inspector_a,
    )
    second = publish_ai_fact_candidates(
        inspection=inspection,
        primary_material=material,
        input_revision=1,
        output=output,
        created_by=inspector_a,
    )
    assert [fact.id for fact in first] == [fact.id for fact in second]
    assert Fact.objects.count() == 2
    assert set(Fact.objects.values_list("review_state", flat=True)) == {"candidate"}


def test_deleting_source_keeps_fact_and_marks_it_for_verification(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    output = FactExtractionOutput(
        (
            FactCandidate(
                "现场存在堆放物品。",
                (EvidenceReference(material.id, 100, 1_000),),
            ),
        )
    )
    fact = publish_ai_fact_candidates(
        inspection=inspection,
        primary_material=material,
        input_revision=1,
        output=output,
        created_by=inspector_a,
    )[0]
    delete_material(material=material, actor=inspector_a, reason="材料上传错误")
    fact.refresh_from_db()
    assert Fact.objects.filter(pk=fact.pk).exists()
    assert fact.needs_verification is True
    assert fact.revision == 2
    assert fact.media_ranges.count() == 0
    assert fact.revisions.order_by("revision").last().action == "source_deleted"


def test_fact_delete_requires_confirmation_and_keeps_minimized_audit(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    created = authenticated_client(inspector_a).post(
        fact_list_url(inspection), {"content": "待删除事实"}, format="json"
    )
    url = f"{fact_list_url(inspection)}/{created.json()['id']}"
    refused = authenticated_client(inspector_a).delete(
        url, {"confirm": False, "reason": "录入错误"}, format="json"
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    deleted = authenticated_client(inspector_a).delete(
        url, {"confirm": True, "reason": "录入错误"}, format="json"
    )
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert AuditEvent.objects.filter(
        object_type="fact", object_id=created.json()["id"], action="deleted"
    ).exists()
    deletion = DomainChangeHistory.objects.get(
        object_type="fact", object_id=created.json()["id"], action="deleted"
    )
    assert deletion.old_values["content"] == "待删除事实"
    assert deletion.new_values == {}
