from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone
from rest_framework import status

from fire_inspect.ai.schemas import (
    FactCandidate,
    FactExtractionOutput,
    SemanticRole,
    TranscriptOutput,
    TranscriptSegment,
)
from fire_inspect.audit.models import AuditEvent
from fire_inspect.facts.models import Fact
from fire_inspect.facts.services import publish_ai_fact_candidates
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import LostLease, acquire_job, enqueue_job
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.services import delete_material
from fire_inspect.transcripts.services import publish_transcript_output
from tests.domain_fixtures import create_media_material
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def _transcript(material, actor):
    return publish_transcript_output(
        TranscriptOutput(
            full_text="问：现场情况？\n答：需要核实。",
            segments=(
                TranscriptSegment(
                    material.id,
                    0,
                    material.duration_ms,
                    "需要核实。",
                    SemanticRole.UNKNOWN,
                ),
            ),
        ),
        updated_by=actor,
    )[0]


def test_admin_compatibility_routes_and_optional_display_name(
    authenticated_client, admin_user, inspector_a
) -> None:
    admin = authenticated_client(admin_user)
    created = admin.post(
        "/api/v1/admin/users/",
        {
            "username": "new-inspector",
            "display_name": "检查员乙",
            "password": "Another-Strong-Password-42!",
            "role": "inspector",
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    assert created.json()["display_name"] == "检查员乙"
    assert admin.get("/api/v1/admin/users/").status_code == status.HTTP_200_OK
    assert (
        authenticated_client(inspector_a).get("/api/v1/admin/users/").status_code
        == status.HTTP_403_FORBIDDEN
    )


def test_inspection_summary_delete_impact_alias_and_admin_jobs(
    authenticated_client, inspector_a, admin_user
) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="人工内容",
    )
    Fact.objects.create(
        inspection=inspection,
        content="人工事实",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_a,
    )
    enqueue_job(
        task_type=ProcessingJob.TaskType.FACT_EXTRACTION,
        scope_type="material",
        scope_id=material.id,
        inspection=inspection,
        input_fingerprint="summary-contract",
        idempotency_key="summary-contract",
        created_by=inspector_a,
    )

    owner = authenticated_client(inspector_a)
    listing = owner.get("/api/v1/inspections/")
    assert listing.status_code == status.HTTP_200_OK
    assert listing.json()[0]["processing_summary"]["queued"] == 1
    impact = owner.get(f"/api/v1/inspections/{inspection.id}/delete-impact/")
    assert impact.status_code == status.HTTP_200_OK
    assert impact.json()["materials"] == 1
    assert impact.json()["facts"] == 1
    assert impact.json()["jobs"] == 1

    assert owner.get("/api/v1/admin/jobs/").status_code == status.HTTP_403_FORBIDDEN
    jobs = authenticated_client(admin_user).get("/api/v1/admin/jobs/")
    assert jobs.status_code == status.HTTP_200_OK
    assert jobs.json()[0]["inspection_id"] == str(inspection.id)


def test_if_match_is_used_consistently_for_transcript_fact_and_segment_updates(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript = _transcript(material, inspector_a)
    segment = transcript.segments.get()
    fact = Fact.objects.create(
        inspection=inspection,
        content="原事实",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_a,
    )
    client = authenticated_client(inspector_a)

    full_text = client.patch(
        f"/api/v1/transcripts/{transcript.id}/",
        {"full_text": "人工连续全文"},
        format="json",
        HTTP_IF_MATCH='"1"',
    )
    assert full_text.status_code == status.HTTP_200_OK
    assert full_text.json()["revision"] == 2

    segment_update = client.patch(
        f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}/",
        {"text": "人工答句", "confirmed_role": "answer"},
        format="json",
        HTTP_IF_MATCH='"2"',
    )
    assert segment_update.status_code == status.HTTP_200_OK
    assert segment_update.json()["text"] == "人工答句"
    assert segment_update.json()["confirmed_role"] == "answer"
    assert segment_update.json()["transcript_revision"] == 3

    fact_update = client.patch(
        f"/api/v1/inspections/{inspection.id}/facts/{fact.id}/",
        {"content": "修改后事实"},
        format="json",
        HTTP_IF_MATCH='"1"',
    )
    assert fact_update.status_code == status.HTTP_200_OK
    confirmed = client.post(
        f"/api/v1/inspections/{inspection.id}/facts/{fact.id}/confirm/",
        {"expected_revision": 2},
        format="json",
    )
    assert confirmed.status_code == status.HTTP_200_OK
    assert confirmed.json()["review_state"] == Fact.ReviewState.CONFIRMED


def test_revision_and_semantic_role_inputs_reject_bool_nonpositive_and_containers(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript = _transcript(material, inspector_a)
    segment = transcript.segments.get()
    client = authenticated_client(inspector_a)

    boolean_revision = client.patch(
        f"/api/v1/transcripts/{transcript.id}/",
        {"full_text": "不能以布尔值作为修订号", "expected_revision": True},
        format="json",
    )
    assert boolean_revision.status_code == status.HTTP_400_BAD_REQUEST
    assert boolean_revision.json()["error"]["code"] == "invalid_revision"

    fractional_revision = client.patch(
        f"/api/v1/transcripts/{transcript.id}/",
        {"full_text": "不能以小数作为修订号", "expected_revision": 1.5},
        format="json",
    )
    assert fractional_revision.status_code == status.HTTP_400_BAD_REQUEST
    assert fractional_revision.json()["error"]["code"] == "invalid_revision"

    nonpositive_revision = client.patch(
        f"/api/v1/inspections/{inspection.id}/",
        {"unit_name": "无效更新", "shared_revision": 0},
        format="json",
    )
    assert nonpositive_revision.status_code == status.HTTP_400_BAD_REQUEST
    assert nonpositive_revision.json()["error"]["code"] == "invalid_revision"

    container_role = client.patch(
        f"/api/v1/transcripts/{transcript.id}/segments/{segment.id}/",
        {"confirmed_role": ["answer"]},
        format="json",
        HTTP_IF_MATCH='"1"',
    )
    assert container_role.status_code == status.HTTP_400_BAD_REQUEST
    assert container_role.json()["error"]["code"] == "invalid_segment_text"


def test_inspection_transcripts_and_evidence_candidates_are_scoped_and_linkable(
    authenticated_client, inspector_a, inspector_b, settings
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(
        inspection=inspection,
        created_by=inspector_a,
        name="scene.mp4",
    )
    transcript = _transcript(material, inspector_a)
    segment = transcript.segments.get()
    frame_path = Path(settings.MEDIA_ROOT) / "derived" / "frame.jpg"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"frame")
    frame = MaterialDerivative.objects.create(
        material=material,
        kind=MaterialDerivative.Kind.KEYFRAME,
        storage_key=frame_path.relative_to(settings.MEDIA_ROOT).as_posix(),
        mime_type="image/jpeg",
        sha256="a" * 64,
        byte_size=5,
        source_timestamp_ms=2_000,
    )
    photo = Material.objects.create(
        inspection=inspection,
        kind=Material.Kind.IMAGE,
        display_name="现场照片.jpg",
        original_file=f"originals/inspections/{inspection.id}/{uuid4()}/original",
        mime_type="image/jpeg",
        extension=".jpg",
        sha256="b" * 64,
        byte_size=5,
        width=10,
        height=10,
        created_by=inspector_a,
    )
    fact = Fact.objects.create(
        inspection=inspection,
        content="待关联证据",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_a,
    )
    owner = authenticated_client(inspector_a)

    transcripts = owner.get(f"/api/v1/inspections/{inspection.id}/transcripts/")
    assert transcripts.status_code == status.HTTP_200_OK
    assert transcripts.json()[0]["material_name"] == "scene.mp4"
    assert (
        authenticated_client(inspector_b)
        .get(f"/api/v1/inspections/{inspection.id}/transcripts/")
        .status_code
        == status.HTTP_404_NOT_FOUND
    )

    owner = authenticated_client(inspector_a)
    response = owner.get(f"/api/v1/inspections/{inspection.id}/evidence-candidates/")
    assert response.status_code == status.HTTP_200_OK
    candidates = response.json()
    ids = {item["id"] for item in candidates}
    expected = {
        f"transcript:{segment.id}",
        f"keyframe:{frame.id}",
        f"photo:{photo.id}",
    }
    assert expected <= ids

    revision = 1
    for candidate_id in expected:
        linked = owner.post(
            f"/api/v1/inspections/{inspection.id}/facts/{fact.id}/evidence/",
            {"id": candidate_id},
            format="json",
            HTTP_IF_MATCH=f'"{revision}"',
            HTTP_IDEMPOTENCY_KEY=f"link-{candidate_id}",
        )
        assert linked.status_code == status.HTTP_200_OK, linked.data
        revision += 1
    assert {item["type"] for item in linked.json()["evidence"]} == {
        "transcript",
        "keyframe",
        "photo",
    }


def test_deleting_primary_source_without_media_range_marks_fact_and_records_revision(
    inspector_a,
) -> None:
    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    fact = publish_ai_fact_candidates(
        inspection=inspection,
        primary_material=material,
        input_revision=material.revision,
        output=FactExtractionOutput(
            (
                FactCandidate(
                    content="仅有待核实来源",
                    evidence=(),
                    needs_verification=True,
                    verification_note="缺少时间锚。",
                ),
            )
        ),
        created_by=inspector_a,
    )[0]
    assert fact.media_ranges.count() == 0

    delete_material(material=material, actor=inspector_a, reason="来源错误")

    fact.refresh_from_db()
    assert fact.primary_source_material_id is None
    assert fact.needs_verification is True
    assert fact.revision == 2
    assert fact.revisions.order_by("revision").last().action == "source_deleted"


def test_stale_lease_is_rejected_at_result_publication_boundary(inspector_a) -> None:
    from fire_inspect.jobs.services import assert_active_lease

    inspection = create_inspection(inspector_a)
    job = enqueue_job(
        task_type=ProcessingJob.TaskType.VISION,
        scope_type="inspection",
        scope_id=inspection.id,
        inspection=inspection,
        input_fingerprint="publish-fence",
        idempotency_key="publish-fence",
        created_by=inspector_a,
    )[0]
    old = acquire_job(worker_id="old-worker", lease_seconds=30)
    old_token = old.lease_token
    ProcessingJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    current = acquire_job(worker_id="new-worker", lease_seconds=30)

    with pytest.raises(LostLease):
        assert_active_lease(job_id=job.id, lease_token=old_token)
    assert_active_lease(job_id=job.id, lease_token=current.lease_token)


def test_audit_hash_chain_protects_event_timestamp(inspector_a) -> None:
    event = AuditEvent.append(
        actor=inspector_a,
        object_type="inspection",
        object_id=None,
        action="viewed",
        masked_change_summary="inspection viewed",
    )
    tampered = event.occurred_at + timedelta(days=1)
    table = connection.ops.quote_name(AuditEvent._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {table} SET occurred_at = %s WHERE id = %s",  # noqa: S608
            [tampered, event.id.hex],
        )
    with pytest.raises(CommandError, match="审计链校验失败"):
        call_command("verify_audit_chain")


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/materials/------------------------------------/download/",
        "/api/v1/transcripts/------------------------------------/",
        "/api/v1/jobs/------------------------------------/",
        "/api/v1/inspections/------------------------------------/facts/",
    ),
)
def test_malformed_uuid_never_reaches_an_unhandled_database_error(
    authenticated_client, inspector_a, path
) -> None:
    response = authenticated_client(inspector_a).get(path)
    assert response.status_code == status.HTTP_404_NOT_FOUND
