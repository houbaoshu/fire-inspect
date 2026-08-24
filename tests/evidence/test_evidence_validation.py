from __future__ import annotations

import pytest
from rest_framework import status

from fire_inspect.evidence.models import FactMediaRange
from tests.domain_fixtures import create_media_material
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def test_cross_inspection_evidence_is_rejected_with_safe_error(
    authenticated_client, inspector_a
) -> None:
    owned = create_inspection(inspector_a, unit_name="检查一")
    foreign = create_inspection(inspector_a, unit_name="检查二")
    foreign_material = create_media_material(
        inspection=foreign, created_by=inspector_a, name="foreign.mp4"
    )
    response = authenticated_client(inspector_a).post(
        f"/api/v1/inspections/{owned.id}/facts",
        {
            "content": "错误关联测试",
            "evidence": [
                {
                    "material_id": str(foreign_material.id),
                    "start_ms": 0,
                    "end_ms": 1_000,
                }
            ],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "cross_inspection_evidence"
    assert FactMediaRange.objects.count() == 0


def test_out_of_timeline_evidence_is_rejected(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(
        inspection=inspection, created_by=inspector_a, duration_ms=2_000
    )
    response = authenticated_client(inspector_a).post(
        f"/api/v1/inspections/{inspection.id}/facts",
        {
            "content": "超时证据测试",
            "evidence": [
                {
                    "material_id": str(material.id),
                    "start_ms": 0,
                    "end_ms": 6_001,
                }
            ],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "evidence_out_of_range"


def test_malformed_material_id_is_rejected_with_safe_error(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    response = authenticated_client(inspector_a).post(
        f"/api/v1/inspections/{inspection.id}/facts",
        {
            "content": "错误材料标识测试",
            "evidence": [{"material_id": "not-a-uuid", "start_ms": 0, "end_ms": 1_000}],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "invalid_material_id"
    assert FactMediaRange.objects.count() == 0


def test_fact_response_preserves_material_specific_time_anchor(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    first = create_media_material(inspection=inspection, created_by=inspector_a, name="first.mp4")
    second = create_media_material(inspection=inspection, created_by=inspector_a, name="second.mp4")
    response = authenticated_client(inspector_a).post(
        f"/api/v1/inspections/{inspection.id}/facts",
        {
            "content": "第二段视频中的事实",
            "evidence": [
                {
                    "material_id": str(second.id),
                    "start_ms": 2_000,
                    "end_ms": 4_000,
                    "note": "第二段材料",
                }
            ],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    evidence = response.json()["evidence"][0]
    assert evidence["type"] == "media_range"
    assert evidence["label"].startswith("second.mp4")
    assert {key: evidence[key] for key in ("id", "material_id", "start_ms", "end_ms", "note")} == {
        "id": evidence["id"],
        "material_id": str(second.id),
        "start_ms": 2_000,
        "end_ms": 4_000,
        "note": "第二段材料",
    }
    assert evidence["material_id"] != str(first.id)
