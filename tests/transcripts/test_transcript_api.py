from __future__ import annotations

import pytest
from rest_framework import status

from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment
from fire_inspect.audit.models import DomainChangeHistory
from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord
from fire_inspect.transcripts.services import publish_transcript_output
from tests.domain_fixtures import create_media_material
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def transcript_output(material) -> TranscriptOutput:
    return TranscriptOutput(
        full_text="问：现场情况如何？\n答：需要进一步核实。",
        segments=(
            TranscriptSegment(material.id, 0, 4_000, "现场情况如何？", SemanticRole.QUESTION),
            TranscriptSegment(material.id, 4_000, 10_000, "需要进一步核实。", SemanticRole.ANSWER),
        ),
    )


def test_publish_transcript_is_idempotent_and_time_anchored(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript, created = publish_transcript_output(
        transcript_output(material), updated_by=inspector_a
    )
    assert created is True
    assert transcript.material_id == material.id
    assert transcript.revision == 1
    assert list(transcript.segments.values_list("start_ms", "end_ms")) == [
        (0, 4_000),
        (4_000, 10_000),
    ]
    repeated, created = publish_transcript_output(
        transcript_output(material), updated_by=inspector_a
    )
    assert created is False
    assert repeated.id == transcript.id
    assert Transcript.objects.count() == 1
    assert TranscriptSegmentRecord.objects.count() == 2
    assert list(
        transcript.material.inspection.domain_history.filter(
            object_type="transcript", object_id=transcript.id
        ).values_list("action", flat=True)
    ) == ["created"]


def test_owner_edits_text_and_confirms_roles_with_history(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript = publish_transcript_output(transcript_output(material), updated_by=inspector_a)[0]
    segment = transcript.segments.order_by("sequence").last()
    client = authenticated_client(inspector_a)

    full_text = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        {"full_text": "人工修正后的连续全文", "revision": 1},
        format="json",
    )
    assert full_text.status_code == status.HTTP_200_OK
    assert full_text.json()["revision"] == 2
    assert full_text.json()["full_text_origin"] == "manual"

    edited = client.patch(
        f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}",
        {"text": "人工修正答句", "transcript_revision": 2},
        format="json",
    )
    assert edited.status_code == status.HTTP_200_OK
    assert edited.json()["text_origin"] == "manual"
    assert edited.json()["transcript_revision"] == 3

    confirmed = client.post(
        f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}/confirm-role",
        {"role": "answer", "transcript_revision": 3},
        format="json",
    )
    assert confirmed.status_code == status.HTTP_200_OK
    assert confirmed.json()["confirmed_role"] == "answer"
    assert confirmed.json()["transcript_revision"] == 4

    history = client.get(f"/api/v1/transcripts/{transcript.id}/history")
    assert history.status_code == status.HTTP_200_OK
    assert [item["action"] for item in history.json()] == [
        "created",
        "full_text_edited",
        "segment_text_edited",
        "segment_role_confirmed",
    ]
    assert all(item["actor"] == inspector_a.username for item in history.json())


def test_owner_can_create_time_anchored_manual_transcript_when_asr_is_unavailable(
    authenticated_client, inspector_a, inspector_b
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    client = authenticated_client(inspector_a)

    created = client.put(
        f"/api/v1/materials/{material.id}/transcript",
        {"full_text": "人工补充：现场情况需要继续核实。"},
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    payload = created.json()
    assert payload["full_text_origin"] == "manual"
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["start_ms"] == 0
    assert payload["segments"][0]["end_ms"] == material.duration_ms
    assert payload["segments"][0]["text_origin"] == "manual"
    assert payload["segments"][0]["confirmed_role"] is None
    assert DomainChangeHistory.objects.get(object_id=payload["id"]).action == "manual_created"

    repeated = client.put(
        f"/api/v1/materials/{material.id}/transcript",
        {"full_text": "不能覆盖现有人工转写"},
        format="json",
    )
    assert repeated.status_code == status.HTTP_409_CONFLICT
    assert repeated.json()["error"]["code"] == "transcript_already_exists"
    assert (
        authenticated_client(inspector_b)
        .put(
            f"/api/v1/materials/{material.id}/transcript",
            {"full_text": "越权补充"},
            format="json",
        )
        .status_code
        == status.HTTP_404_NOT_FOUND
    )


def test_manual_transcript_rejects_incomplete_timeline(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    response = authenticated_client(inspector_a).put(
        f"/api/v1/materials/{material.id}/transcript",
        {
            "full_text": "只有中间一小段",
            "segments": [
                {
                    "start_ms": 4_000,
                    "end_ms": 5_000,
                    "text": "只有中间一小段",
                    "confirmed_role": "answer",
                }
            ],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "invalid_transcript"
    assert not Transcript.objects.filter(material=material).exists()


def test_transcript_revision_conflict_and_unknown_role_rejected(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript = publish_transcript_output(transcript_output(material), updated_by=inspector_a)[0]
    segment = transcript.segments.first()
    client = authenticated_client(inspector_a)
    stale = client.patch(
        f"/api/v1/transcripts/{transcript.id}",
        {"full_text": "修改", "revision": 99},
        format="json",
    )
    assert stale.status_code == status.HTTP_409_CONFLICT
    assert stale.json()["error"]["code"] == "revision_conflict"
    invalid = client.post(
        f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}/confirm-role",
        {"role": "unknown", "transcript_revision": 1},
        format="json",
    )
    assert invalid.status_code == status.HTTP_400_BAD_REQUEST


def test_cross_user_cannot_read_or_edit_transcript(
    authenticated_client, inspector_a, inspector_b
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript = publish_transcript_output(transcript_output(material), updated_by=inspector_a)[0]
    other = authenticated_client(inspector_b)
    assert other.get(f"/api/v1/materials/{material.id}/transcript").status_code == 404
    assert other.get(f"/api/v1/transcripts/{transcript.id}").status_code == 404
    assert (
        other.patch(
            f"/api/v1/transcripts/{transcript.id}",
            {"full_text": "越权修改", "revision": 1},
            format="json",
        ).status_code
        == 404
    )
    assert not DomainChangeHistory.objects.filter(actor=inspector_b).exists()
