from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image

from fire_inspect.documents import DocumentRenderError
from fire_inspect.documents.models import (
    DocumentExport,
    DocumentFieldValue,
    DocumentRevision,
    DocumentStateEvent,
    InspectionDocument,
    PhotoDocumentItem,
)
from fire_inspect.documents.services import (
    RenderedDocument,
    initialize_document_fields,
    merge_generated_document_fields,
)
from fire_inspect.facts.models import Fact
from fire_inspect.facts.services import edit_fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.inspections.services import update_inspection
from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.services import delete_material
from fire_inspect.templates.services import initialize_authoritative_templates
from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord

pytestmark = pytest.mark.django_db


@pytest.fixture
def inspection(inspector_a):
    return Inspection.objects.create(
        owner=inspector_a,
        unit_name="滨江商场",
        unit_address="海州市滨江路 18 号",
        responsible_person="王某",
        contact_name="赵某",
        contact_phone="13800000000",
        inspection_at=timezone.make_aware(datetime(2026, 8, 24, 10, 30)),
        inspection_type=Inspection.Type.CONSTRUCTION_SITE,
        general_notes="现场一般说明",
    )


@pytest.fixture
def authoritative_templates(admin_user):
    return initialize_authoritative_templates(actor=admin_user)


def _create_document(client, inspection, document_type):
    return client.post(
        f"/api/v1/inspections/{inspection.id}/documents/",
        {"document_type": document_type},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"create-{document_type}",
    )


def _png_bytes(size=(320, 180), color=(70, 120, 180)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _image_material(inspection, actor, *, label="现场照片.png"):
    payload = _png_bytes()
    material = Material(
        inspection=inspection,
        kind=Material.Kind.IMAGE,
        display_name=label,
        mime_type="image/png",
        extension=".png",
        sha256="a" * 64,
        byte_size=len(payload),
        width=320,
        height=180,
        created_by=actor,
    )
    material.original_file.save("original", ContentFile(payload), save=False)
    material.save()
    return material


def _run_next_document_generation(client, document_id, *, worker_id: str) -> ProcessingJob:
    claimed = acquire_job(
        worker_id=worker_id,
        task_types=[ProcessingJob.TaskType.DOCUMENT_GENERATION],
    )
    assert claimed is not None
    assert str(claimed.scope_id) == str(document_id)
    running = client.get(f"/api/v1/documents/{document_id}/")
    assert running.status_code == 200, running.data
    assert running.data["generator_status"] == InspectionDocument.GeneratorStatus.RUNNING
    assert running.data["generator_job_id"] == str(claimed.id)
    WorkerCommand._execute(claimed.id, claimed.lease_token)
    claimed.refresh_from_db()
    return claimed


def test_document_create_is_scoped_idempotent_and_uses_default_name(
    authenticated_client,
    inspector_a,
    inspector_b,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    first = _create_document(client, inspection, "record")
    second = _create_document(client, inspection, "record")
    assert first.status_code == 201, first.data
    assert second.status_code == 200, second.data
    assert second.data["id"] == first.data["id"]
    assert first.data["name"] == "滨江商场消防监督检查记录"
    assert first.data["state"] == "draft"

    other = authenticated_client(inspector_b)
    assert other.get(f"/api/v1/documents/{first.data['id']}/").status_code == 404


def test_field_edit_requires_revision_rejects_shared_override_and_locks_manual_value(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    created = _create_document(client, inspection, "record")
    document_id = created.data["id"]
    revision = created.data["revision"]

    missing_revision = client.patch(
        f"/api/v1/documents/{document_id}/fields/record_number/",
        {"value": "海消检〔2026〕18号", "origin": "manual", "manual_locked": True},
        format="json",
    )
    assert missing_revision.status_code == 428

    shared = client.patch(
        f"/api/v1/documents/{document_id}/fields/unit_name/",
        {"value": "局部覆盖"},
        format="json",
        HTTP_IF_MATCH=f'"{revision}"',
    )
    assert shared.status_code == 400
    assert shared.data["code"] == "shared_field_override_forbidden"

    edited = client.patch(
        f"/api/v1/documents/{document_id}/fields/record_number/",
        {"value": "海消检〔2026〕18号", "origin": "manual", "manual_locked": True},
        format="json",
        HTTP_IF_MATCH=f'"{revision}"',
    )
    assert edited.status_code == 200, edited.data
    assert edited.data["origin"] == "manual"
    assert edited.data["manual_locked"] is True
    assert edited.data["value"] == "海消检〔2026〕18号"

    conflict = client.patch(
        f"/api/v1/documents/{document_id}/fields/inspection_content/",
        {"value": "使用过期修订号"},
        format="json",
        HTTP_IF_MATCH=f'"{revision}"',
    )
    assert conflict.status_code == 409
    assert conflict.data["code"] == "revision_conflict"


def test_generated_field_merge_preserves_manual_content_unless_explicitly_overridden(
    inspector_a, inspection, authoritative_templates
):
    document = InspectionDocument.objects.create(
        inspection=inspection,
        document_type=InspectionDocument.Type.RECORD,
        name="记录",
        template_version=authoritative_templates["record"].active_mapping_revision.template_version,
        current_mapping_revision=authoritative_templates["record"].active_mapping_revision,
    )
    initialize_document_fields(document=document, actor=inspector_a)
    field = document.field_values.get(business_field_key="inspection_content")
    field.value_json = "人工核对后的内容"
    field.origin = DocumentFieldValue.Origin.MANUAL
    field.manual_locked = True
    field.save()

    result = merge_generated_document_fields(
        document=document,
        generated={"inspection_content": "AI 新内容", "record_number": "系统编号"},
        actor=inspector_a,
    )
    assert "inspection_content" in result.preserved_manual_keys
    field.refresh_from_db()
    assert field.value_json == "人工核对后的内容"

    result = merge_generated_document_fields(
        document=document,
        generated={"inspection_content": "经人工显式同意覆盖"},
        actor=inspector_a,
        explicit_manual_overrides={"inspection_content"},
    )
    assert "inspection_content" in result.overwritten_manual_keys
    field.refresh_from_db()
    assert field.value_json == "经人工显式同意覆盖"
    assert field.origin == DocumentFieldValue.Origin.AI


def test_shared_field_change_invalidates_all_three_confirmed_documents_synchronously(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    documents = [
        InspectionDocument.objects.get(pk=_create_document(client, inspection, kind).data["id"])
        for kind in ("record", "photo", "interview")
    ]
    InspectionDocument.objects.filter(pk__in=[item.pk for item in documents]).update(
        state=InspectionDocument.State.CONFIRMED
    )

    update_inspection(
        inspection_id=inspection.id,
        actor=inspector_a,
        expected_revision=inspection.shared_revision,
        values={"unit_address": "海州市滨江路 20 号"},
    )

    assert set(
        InspectionDocument.objects.filter(inspection=inspection).values_list("state", flat=True)
    ) == {InspectionDocument.State.DRAFT}
    events = DocumentStateEvent.objects.filter(
        document__inspection=inspection,
        reason_code=DocumentStateEvent.Reason.SHARED_FIELDS_CHANGED,
    )
    assert events.count() == 3


def test_no_effective_shared_change_does_not_invalidate_confirmed_document(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    document = InspectionDocument.objects.get(
        pk=_create_document(client, inspection, "record").data["id"]
    )
    document.state = InspectionDocument.State.CONFIRMED
    document.save(update_fields=("state",))

    update_inspection(
        inspection_id=inspection.id,
        actor=inspector_a,
        expected_revision=inspection.shared_revision,
        values={"unit_address": inspection.unit_address},
    )
    document.refresh_from_db()
    assert document.state == InspectionDocument.State.CONFIRMED
    assert not document.state_events.exists()


def test_photo_items_are_deduplicated_and_only_photo_document_is_invalidated(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    photo = _create_document(client, inspection, "photo").data
    record = _create_document(client, inspection, "record").data
    material = _image_material(inspection, inspector_a)

    first = client.post(
        f"/api/v1/documents/{photo['id']}/photo-items/",
        {"source_ids": [str(material.id), str(material.id)]},
        format="json",
        HTTP_IF_MATCH=f'"{photo["revision"]}"',
    )
    assert first.status_code == 200, first.data
    assert len(first.data["photo_items"]) == 1
    photo_revision = first.data["revision"]
    photo_item = first.data["photo_items"][0]

    InspectionDocument.objects.filter(pk__in=[photo["id"], record["id"]]).update(
        state=InspectionDocument.State.CONFIRMED
    )
    edited = client.patch(
        f"/api/v1/documents/{photo['id']}/photo-items/{photo_item['id']}/",
        {"reflected_content": "安全出口附近堆放物品", "description": "人工复核"},
        format="json",
        HTTP_IF_MATCH=f'"{photo_revision}"',
    )
    assert edited.status_code == 200, edited.data
    assert InspectionDocument.objects.get(pk=photo["id"]).state == "draft"
    assert InspectionDocument.objects.get(pk=record["id"]).state == "confirmed"


def test_photo_candidates_and_selected_items_expose_protected_thumbnail_urls(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
    settings,
):
    client = authenticated_client(inspector_a)
    photo = _create_document(client, inspection, "photo").data
    material = _image_material(inspection, inspector_a)
    storage_key = f"derived/materials/{material.id}/1/test/keyframe.jpg"
    derivative_path = Path(settings.MEDIA_ROOT) / storage_key
    derivative_path.parent.mkdir(parents=True, exist_ok=True)
    derivative_path.write_bytes(b"keyframe")
    derivative = MaterialDerivative.objects.create(
        material=material,
        kind=MaterialDerivative.Kind.KEYFRAME,
        storage_key=storage_key,
        mime_type="image/jpeg",
        sha256="b" * 64,
        byte_size=8,
        source_timestamp_ms=1_500,
    )

    candidates = client.get(f"/api/v1/documents/{photo['id']}/photo-candidates/")
    assert candidates.status_code == 200, candidates.data
    by_id = {item["id"]: item for item in candidates.data}
    assert by_id[str(material.id)]["thumbnail_url"] == (f"/api/v1/materials/{material.id}/preview/")
    assert by_id[str(derivative.id)]["thumbnail_url"] == (
        f"/api/v1/material-derivatives/{derivative.id}/preview/"
    )

    added = client.post(
        f"/api/v1/documents/{photo['id']}/photo-items/",
        {"source_ids": [str(material.id), str(derivative.id)]},
        format="json",
        HTTP_IF_MATCH=f'"{photo["revision"]}"',
    )
    assert added.status_code == 200, added.data
    selected = {item["source_id"]: item for item in added.data["photo_items"]}
    assert selected[str(material.id)]["thumbnail_url"] == (
        f"/api/v1/materials/{material.id}/preview/"
    )
    assert selected[str(derivative.id)]["thumbnail_url"] == (
        f"/api/v1/material-derivatives/{derivative.id}/preview/"
    )


def test_deleting_selected_source_invalidates_only_dependent_document(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    photo = _create_document(client, inspection, "photo").data
    record = _create_document(client, inspection, "record").data
    material = _image_material(inspection, inspector_a)
    added = client.post(
        f"/api/v1/documents/{photo['id']}/photo-items/",
        {"source_ids": [str(material.id)]},
        format="json",
        HTTP_IF_MATCH=f'"{photo["revision"]}"',
    )
    assert added.status_code == 200
    InspectionDocument.objects.filter(pk__in=[photo["id"], record["id"]]).update(
        state=InspectionDocument.State.CONFIRMED
    )

    delete_material(material=material, actor=inspector_a, reason="重复材料")

    assert InspectionDocument.objects.get(pk=photo["id"]).state == "draft"
    assert InspectionDocument.objects.get(pk=record["id"]).state == "confirmed"
    assert PhotoDocumentItem.objects.get(document_id=photo["id"]).source_material_id is None
    assert DocumentStateEvent.objects.filter(
        document_id=photo["id"], reason_code="SOURCE_DELETED"
    ).exists()


@pytest.mark.parametrize("failure_stage", ("render", "publish"))
@pytest.mark.django_db(transaction=True)
def test_document_generation_job_failure_is_atomic_and_retries_same_job(
    monkeypatch,
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
    settings,
    failure_stage,
):
    monkeypatch.setenv("FIRE_INSPECT_LLM_PROVIDER", "fixed")
    monkeypatch.setenv("FIRE_INSPECT_ALLOW_FIXED_AI", "1")
    monkeypatch.setenv("FIRE_INSPECT_ENV", "test")
    client = authenticated_client(inspector_a)
    document = _create_document(client, inspection, "record").data
    for key, value in (
        ("record_number", "海消检〔2026〕18号"),
        ("inspection_content", "检查了安全出口和疏散通道。"),
    ):
        response = client.patch(
            f"/api/v1/documents/{document['id']}/fields/{key}/",
            {"value": value, "origin": "manual", "manual_locked": True},
            format="json",
            HTTP_IF_MATCH=f'"{document["revision"]}"',
        )
        assert response.status_code == 200, response.data
        document["revision"] = response.data["document_revision"]

    render_calls: list[str] = []

    def successful_render(payload, *, document_type):
        render_calls.append(document_type)
        return RenderedDocument(
            payload=payload,
            page_count=1,
            structure_check_result={"passed": True, "type": document_type},
            render_check_result={"passed": True, "font_fidelity": True},
        )

    monkeypatch.setattr(
        "fire_inspect.documents.services.render_generated_document", successful_render
    )
    initial_generation_revision = document["revision"]
    submitted = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{initial_generation_revision}"',
        HTTP_IDEMPOTENCY_KEY="generate-record",
    )
    assert submitted.status_code == 202, submitted.data
    assert submitted.data["generator_status"] == "queued"
    assert submitted.data["generation_valid"] is False
    assert submitted.data["revision"] == initial_generation_revision
    assert render_calls == []
    assert DocumentRevision.objects.filter(document_id=document["id"]).count() == 0
    replayed = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{initial_generation_revision}"',
        HTTP_IDEMPOTENCY_KEY="generate-record",
    )
    assert replayed.status_code == 202, replayed.data
    assert replayed.data["generator_status"] == "queued"
    assert replayed.data["generator_job_id"] == submitted.data["generator_job_id"]
    assert replayed.data["revision"] == initial_generation_revision
    assert render_calls == []
    assert DocumentRevision.objects.filter(document_id=document["id"]).count() == 0
    assert (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.DOCUMENT_GENERATION,
            scope_id=document["id"],
        ).count()
        == 1
    )

    generation_job = ProcessingJob.objects.get(pk=submitted.data["generator_job_id"])
    assert generation_job.status == ProcessingJob.Status.QUEUED
    assert generation_job.attempt_count == 0
    executed = _run_next_document_generation(
        client,
        document["id"],
        worker_id="document-generation-success",
    )
    assert executed.id == generation_job.id
    generation_job.refresh_from_db()
    assert generation_job.status == ProcessingJob.Status.SUCCEEDED
    assert generation_job.attempt_count == 1
    assert render_calls == ["record"]
    generated = client.get(f"/api/v1/documents/{document['id']}/")
    assert generated.status_code == 200, generated.data
    assert generated.data["generator_status"] == "succeeded"
    assert generated.data["generation_valid"] is True
    assert generated.data["revision"] == initial_generation_revision + 1
    assert DocumentRevision.objects.filter(document_id=document["id"]).count() == 1
    document = generated.data

    confirmed = client.post(
        f"/api/v1/documents/{document['id']}/confirm/",
        {"expected_revision": document["revision"]},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert confirmed.status_code == 200, confirmed.data
    assert confirmed.data["state"] == "confirmed"

    exported = client.post(
        f"/api/v1/documents/{document['id']}/export/",
        {"expected_revision": confirmed.data["revision"]},
        format="json",
        HTTP_IF_MATCH=f'"{confirmed.data["revision"]}"',
        HTTP_IDEMPOTENCY_KEY="export-record",
    )
    assert exported.status_code == 200
    assert exported["Content-Type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    current = InspectionDocument.objects.select_related("last_generated_revision").get(
        pk=document["id"]
    )
    previous_revision = current.last_generated_revision
    assert previous_revision is not None
    previous_export = DocumentExport.objects.get(
        document=current, document_revision=previous_revision
    )
    with default_storage.open(previous_export.storage_key, "rb") as stream:
        previous_payload = stream.read()
    previous_revision_count = current.revisions.count()
    previous_generated_revision_count = current.revisions.filter(
        event_type=DocumentRevision.EventType.GENERATED
    ).count()
    previous_export_count = current.exports.count()

    edited = client.patch(
        f"/api/v1/documents/{document['id']}/fields/record_number/",
        {"value": "海消检〔2026〕19号", "origin": "manual", "manual_locked": True},
        format="json",
        HTTP_IF_MATCH=f'"{confirmed.data["revision"]}"',
    )
    assert edited.status_code == 200, edited.data
    failed_revision = edited.data["document_revision"]
    current.refresh_from_db()
    failed_baseline_revision_count = current.revisions.count()
    assert failed_baseline_revision_count == previous_revision_count + 1

    real_revision_create = DocumentRevision.objects.create

    if failure_stage == "render":

        def fail_generation(_payload, *, document_type):
            del document_type
            raise DocumentRenderError("文书渲染进程异常，请稍后单独重试。")

        monkeypatch.setattr(
            "fire_inspect.documents.services.render_generated_document", fail_generation
        )
    else:

        def fail_publication(**_kwargs):
            raise OSError("simulated atomic publication failure")

        monkeypatch.setattr(DocumentRevision.objects, "create", fail_publication)

    failed_submission = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{failed_revision}"',
        HTTP_IDEMPOTENCY_KEY=f"generate-record-{failure_stage}-failure",
    )
    assert failed_submission.status_code == 202, failed_submission.data
    assert failed_submission.data["generator_status"] == "queued"
    assert failed_submission.data["revision"] == failed_revision

    failed_job = ProcessingJob.objects.get(pk=failed_submission.data["generator_job_id"])
    assert failed_job.status == ProcessingJob.Status.QUEUED
    assert failed_job.attempt_count == 0
    executed = _run_next_document_generation(
        client,
        document["id"],
        worker_id=f"document-generation-{failure_stage}-failure",
    )
    assert executed.id == failed_job.id

    current.refresh_from_db()
    assert current.generator_status == InspectionDocument.GeneratorStatus.FAILED
    assert current.generator_job_id is not None
    assert current.generator_safe_error
    assert current.generation_valid is False
    assert current.last_generated_revision_id == previous_revision.id
    assert current.revisions.count() == failed_baseline_revision_count
    assert current.exports.count() == previous_export_count
    assert DocumentExport.objects.get(pk=previous_export.id).document_revision_id == (
        previous_revision.id
    )
    with default_storage.open(previous_export.storage_key, "rb") as stream:
        assert stream.read() == previous_payload
    assert default_storage.exists(previous_revision.generated_storage_key)
    published_files = list((Path(settings.MEDIA_ROOT) / "documents").rglob("document.docx"))
    assert len(published_files) == previous_generated_revision_count

    failed_job.refresh_from_db()
    assert failed_job.task_type == ProcessingJob.TaskType.DOCUMENT_GENERATION
    assert failed_job.scope_type == "document"
    assert failed_job.scope_id == current.id
    assert failed_job.status == ProcessingJob.Status.FAILED
    assert failed_job.attempt_count == 1
    assert list(failed_job.attempts.values_list("outcome", flat=True)) == ["failed"]

    repeated = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{failed_revision}"',
        HTTP_IDEMPOTENCY_KEY=f"generate-record-{failure_stage}-failure",
    )
    assert repeated.status_code == 202
    assert repeated.data["generator_status"] == InspectionDocument.GeneratorStatus.FAILED
    assert repeated.data["generator_job_id"] == str(failed_job.id)
    failed_job.refresh_from_db()
    assert failed_job.attempt_count == 1
    assert (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.DOCUMENT_GENERATION,
            scope_id=current.id,
        ).count()
        == 2
    )

    stale = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{failed_revision - 1}"',
        HTTP_IDEMPOTENCY_KEY=f"generate-record-{failure_stage}-stale",
    )
    assert stale.status_code == 409
    assert (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.DOCUMENT_GENERATION,
            scope_id=current.id,
        ).count()
        == 2
    )

    monkeypatch.setattr(
        "fire_inspect.documents.services.render_generated_document", successful_render
    )
    monkeypatch.setattr(DocumentRevision.objects, "create", real_revision_create)
    retried = client.post(
        f"/api/v1/jobs/{failed_job.id}/retry/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"retry-record-{failure_stage}",
    )
    assert retried.status_code == 202, retried.data
    assert retried.data["id"] == str(failed_job.id)
    current.refresh_from_db()
    assert current.generator_status == InspectionDocument.GeneratorStatus.QUEUED
    assert current.generator_job_id == failed_job.id

    executed = _run_next_document_generation(
        client,
        document["id"],
        worker_id=f"document-generation-{failure_stage}-retry",
    )
    assert executed.id == failed_job.id

    failed_job.refresh_from_db()
    current.refresh_from_db()
    assert failed_job.status == ProcessingJob.Status.SUCCEEDED
    assert failed_job.attempt_count == 2
    assert list(failed_job.attempts.order_by("attempt_no").values_list("outcome", flat=True)) == [
        "failed",
        "succeeded",
    ]
    assert current.generator_status == InspectionDocument.GeneratorStatus.SUCCEEDED
    assert current.generator_job_id == failed_job.id
    assert current.generation_valid is True
    assert current.last_generated_revision_id != previous_revision.id
    assert current.revisions.count() == failed_baseline_revision_count + 1
    assert current.exports.count() == previous_export_count
    with default_storage.open(previous_export.storage_key, "rb") as stream:
        assert stream.read() == previous_payload
    assert (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.DOCUMENT_GENERATION,
            scope_id=current.id,
        ).count()
        == 2
    )


def test_interview_creation_snapshots_transcript_as_unconfirmed_qa_candidates(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="询问摘录.txt",
        text="是否有人值班？今天休息。",
    )
    transcript = Transcript.objects.create(
        material=material,
        full_text="是否有人值班？今天休息。",
        updated_by=inspector_a,
    )
    TranscriptSegmentRecord.objects.create(
        transcript=transcript,
        sequence=0,
        start_ms=0,
        end_ms=1_000,
        text="是否有人值班？",
        suggested_role=TranscriptSegmentRecord.SemanticRole.UNKNOWN,
        confirmed_role=TranscriptSegmentRecord.SemanticRole.QUESTION,
        role_confirmed_by=inspector_a,
        role_confirmed_at=timezone.now(),
    )
    TranscriptSegmentRecord.objects.create(
        transcript=transcript,
        sequence=1,
        start_ms=1_000,
        end_ms=2_000,
        text="今天休息。",
        suggested_role=TranscriptSegmentRecord.SemanticRole.ANSWER,
    )

    response = _create_document(authenticated_client(inspector_a), inspection, "interview")
    assert response.status_code == 201, response.data
    assert [item["suggested_role"] for item in response.data["qa_items"]] == [
        "question",
        "answer",
    ]
    assert [item["confirmed_role"] for item in response.data["qa_items"]] == [
        "question",
        None,
    ]


def test_fact_dependency_selection_invalidates_only_documents_that_explicitly_use_fact(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
):
    client = authenticated_client(inspector_a)
    record = _create_document(client, inspection, "record").data
    photo = _create_document(client, inspection, "photo").data
    fact = Fact.objects.create(
        inspection=inspection,
        content="安全出口附近堆放物品",
        review_state=Fact.ReviewState.CONFIRMED,
        origin=Fact.Origin.MANUAL,
        confirmed_by=inspector_a,
        confirmed_at=timezone.now(),
        created_by=inspector_a,
    )
    selected = client.put(
        f"/api/v1/documents/{record['id']}/fact-dependencies/",
        {"fact_ids": [str(fact.id)]},
        format="json",
        HTTP_IF_MATCH=f'"{record["revision"]}"',
    )
    assert selected.status_code == 200, selected.data
    InspectionDocument.objects.filter(pk__in=(record["id"], photo["id"])).update(
        state=InspectionDocument.State.CONFIRMED
    )

    edit_fact(
        fact_id=fact.id,
        actor=inspector_a,
        expected_revision=fact.revision,
        values={"content": "安全出口和疏散通道附近堆放物品"},
    )

    assert InspectionDocument.objects.get(pk=record["id"]).state == "draft"
    assert InspectionDocument.objects.get(pk=photo["id"]).state == "confirmed"
