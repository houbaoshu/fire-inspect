from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from fire_inspect.audit.models import AuditEvent, DomainChangeHistory
from fire_inspect.documents.handlers import enqueue_document_generation
from fire_inspect.documents.models import (
    DocumentRevision,
    DocumentStateEvent,
    InspectionDocument,
)
from fire_inspect.documents.services import (
    RenderedDocument,
    _invalidate_one_locked,
    add_photo_sources,
    confirm_document,
    create_qa_item,
    delete_photo_item,
    delete_qa_item,
    export_document,
    initialize_document_fields,
    reorder_qa_items,
    replace_fact_dependencies,
    sync_qa_candidates,
    update_photo_item,
    update_qa_item,
)
from fire_inspect.facts.models import Fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job_by_id
from fire_inspect.materials.models import Material
from fire_inspect.templates.models import (
    TemplateMappingRevision,
    TemplateVersion,
    WordTemplate,
)
from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
SECRET_TEXT = "绝密现场描述-ALPHA"


@pytest.fixture
def document_factory(inspector_a) -> Callable[[str], InspectionDocument]:
    templates: dict[str, tuple[TemplateVersion, TemplateMappingRevision]] = {}
    filenames = {
        InspectionDocument.Type.RECORD: "监督检查记录表.docx",
        InspectionDocument.Type.PHOTO: "照片.docx",
        InspectionDocument.Type.INTERVIEW: "询问笔录.docx",
    }
    counter = 0

    def factory(document_type: str) -> InspectionDocument:
        nonlocal counter
        counter += 1
        if document_type not in templates:
            payload = (ROOT / "docs/templates" / filenames[document_type]).read_bytes()
            template = WordTemplate.objects.create(
                document_type=document_type,
                name=f"审计测试模板-{document_type}",
                recognition_status=WordTemplate.RecognitionStatus.SUCCEEDED,
                created_by=inspector_a,
            )
            version = TemplateVersion(
                template=template,
                version_no=1,
                original_filename=filenames[document_type],
                sha256=hashlib.sha256(payload).hexdigest(),
                validation_result={"passed": True},
                created_by=inspector_a,
            )
            version.original_file.save("original.docx", ContentFile(payload), save=False)
            version.save()
            mapping = TemplateMappingRevision.objects.create(
                template_version=version,
                revision_no=1,
                state=TemplateMappingRevision.State.CONFIRMED,
                recognizer_version="audit-test-v1",
                created_by=inspector_a,
                confirmed_by=inspector_a,
                confirmed_at=timezone.now(),
            )
            template.active_mapping_revision = mapping
            template.save(update_fields=("active_mapping_revision",))
            templates[document_type] = (version, mapping)
        version, mapping = templates[document_type]
        inspection = Inspection.objects.create(
            owner=inspector_a,
            unit_name=f"审计测试单位-{counter}",
            unit_address="海州市滨江路 18 号",
            responsible_person="负责人",
            contact_name="联系人",
            contact_phone="13800000000",
            inspection_at=timezone.make_aware(datetime(2026, 8, 24, 10, 30)),
            inspection_type=Inspection.Type.CONSTRUCTION_SITE,
            general_notes=SECRET_TEXT,
        )
        document = InspectionDocument.objects.create(
            inspection=inspection,
            document_type=document_type,
            name=f"审计测试文书-{counter}",
            template_version=version,
            current_mapping_revision=mapping,
            created_by=inspector_a,
        )
        initialize_document_fields(document=document, actor=inspector_a)
        return document

    return factory


def _make_generated(document: InspectionDocument, actor) -> DocumentRevision:
    payload = b"PK\x03\x04audit-document"
    key = f"documents/{document.id}/audit-test/document.docx"
    default_storage.save(key, ContentFile(payload))
    revision = DocumentRevision.objects.create(
        document=document,
        revision_no=1,
        document_revision_no=document.revision,
        event_type=DocumentRevision.EventType.GENERATED,
        content_snapshot={"fields": {}},
        input_revision_vector={},
        mapping_revision=document.current_mapping_revision,
        generator_version=document.generator_version,
        generated_storage_key=key,
        generated_sha256=hashlib.sha256(payload).hexdigest(),
        page_count=1,
        structure_check_result={"passed": True},
        render_check_result={"passed": True},
        created_by=actor,
    )
    document.last_generated_revision = revision
    document.generator_status = InspectionDocument.GeneratorStatus.SUCCEEDED
    document.generation_valid = True
    document.save(
        update_fields=(
            "last_generated_revision",
            "generator_status",
            "generation_valid",
            "updated_at",
        )
    )
    return revision


def _assert_detailed_history(history: DomainChangeHistory, *, actor, reason: str) -> None:
    assert history.actor == actor
    assert history.created_at is not None
    assert history.old_values
    assert history.new_values
    assert history.reason == reason


def _assert_minimized_audit(event: AuditEvent) -> None:
    assert SECRET_TEXT not in event.masked_change_summary
    assert SECRET_TEXT not in event.reason
    assert "13800000000" not in event.masked_change_summary
    assert "13800000000" not in event.reason


def test_confirm_is_detailed_minimized_revisioned_and_idempotent(
    document_factory, inspector_a
) -> None:
    document = document_factory(InspectionDocument.Type.RECORD)
    _make_generated(document, inspector_a)

    confirmed = confirm_document(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
    )
    replayed = confirm_document(
        document=confirmed,
        actor=inspector_a,
        expected_revision=confirmed.revision,
    )
    assert replayed.state == InspectionDocument.State.CONFIRMED

    history = DomainChangeHistory.objects.get(
        object_type="document", object_id=document.id, action="state_confirmed"
    )
    _assert_detailed_history(history, actor=inspector_a, reason="用户显式确认文书")
    assert history.old_values["state"] == "draft"
    assert history.new_values["state"] == "confirmed"
    event = AuditEvent.objects.get(
        object_type="document", object_id=document.id, action="state_confirmed"
    )
    _assert_minimized_audit(event)
    assert (
        DocumentRevision.objects.filter(
            document=document,
            event_type=DocumentRevision.EventType.CONFIRMED,
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    "reason_code",
    [
        DocumentStateEvent.Reason.SHARED_FIELDS_CHANGED,
        DocumentStateEvent.Reason.REFERENCED_FACT_CHANGED,
        DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
        DocumentStateEvent.Reason.QA_BODY_CHANGED,
        DocumentStateEvent.Reason.TEMPLATE_MAPPING_CHANGED,
        DocumentStateEvent.Reason.DOCUMENT_FIELD_EDITED,
        DocumentStateEvent.Reason.SOURCE_DELETED,
    ],
)
def test_every_central_invalidation_reason_writes_detailed_minimized_lifecycle_event(
    document_factory,
    inspector_a,
    reason_code,
) -> None:
    document = document_factory(InspectionDocument.Type.RECORD)
    document.state = InspectionDocument.State.CONFIRMED
    document.confirmed_by = inspector_a
    document.confirmed_at = timezone.now()
    document.generation_valid = True
    document.save(
        update_fields=("state", "confirmed_by", "confirmed_at", "generation_valid", "updated_at")
    )
    reason = f"{reason_code} 的详细业务原因：{SECRET_TEXT}"

    with transaction.atomic():
        locked = InspectionDocument.objects.select_for_update().get(pk=document.pk)
        assert _invalidate_one_locked(
            locked,
            reason=reason_code,
            summary=reason,
            caused_by_type="audit_test",
            caused_by_id=document.id,
            actor=inspector_a,
        )
        locked.revision += 1
        locked.save(update_fields=("revision", "updated_at"))

    history = DomainChangeHistory.objects.get(
        object_type="document", object_id=document.id, action="state_invalidated"
    )
    _assert_detailed_history(history, actor=inspector_a, reason=reason)
    assert history.old_values["state"] == "confirmed"
    assert history.new_values["state"] == "draft"
    assert history.new_values["reason_code"] == reason_code
    event = AuditEvent.objects.get(
        object_type="document", object_id=document.id, action="state_invalidated"
    )
    assert event.reason == reason_code
    _assert_minimized_audit(event)
    lifecycle = DocumentRevision.objects.get(
        document=document,
        event_type=DocumentRevision.EventType.INVALIDATED,
    )
    assert lifecycle.content_snapshot["reason_code"] == reason_code


def test_generation_request_publish_failure_and_replay_are_audited_once(
    document_factory,
    inspector_a,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FIRE_INSPECT_LLM_PROVIDER", "fixed")
    monkeypatch.setenv("FIRE_INSPECT_ALLOW_FIXED_AI", "1")
    monkeypatch.setenv("FIRE_INSPECT_ENV", "acceptance")
    monkeypatch.setattr(
        "fire_inspect.documents.services.render_generated_document",
        lambda payload, *, document_type: RenderedDocument(
            payload=payload,
            page_count=1,
            structure_check_result={"passed": True, "document_type": document_type},
            render_check_result={"passed": True},
        ),
    )
    document = document_factory(InspectionDocument.Type.RECORD)
    document.field_values.filter(business_field_key="record_number").update(
        value_json="海消检〔2026〕18号"
    )
    job, created = enqueue_document_generation(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
        idempotency_key="audit-generation-success",
    )
    assert created and job is not None
    replay, replay_created = enqueue_document_generation(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
        idempotency_key="audit-generation-success",
    )
    assert replay == job
    assert replay_created is False
    claimed = acquire_job_by_id(job_id=job.id, worker_id="audit-generation-success")
    assert claimed is not None
    WorkerCommand._execute(claimed.id, claimed.lease_token)
    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.SUCCEEDED

    assert (
        DomainChangeHistory.objects.filter(
            object_id=document.id, action="generation_requested"
        ).count()
        == 1
    )
    published = DomainChangeHistory.objects.get(
        object_id=document.id, action="generation_published"
    )
    _assert_detailed_history(published, actor=inspector_a, reason="文书生成结果已原子发布")
    for event in AuditEvent.objects.filter(
        object_id=document.id,
        action__in=("generation_requested", "generation_published"),
    ):
        _assert_minimized_audit(event)

    monkeypatch.setenv("FIRE_INSPECT_LLM_PROVIDER", "disabled")
    failed_document = document_factory(InspectionDocument.Type.RECORD)
    failed_job, created = enqueue_document_generation(
        document=failed_document,
        actor=inspector_a,
        expected_revision=failed_document.revision,
        idempotency_key="audit-generation-failure",
    )
    assert created and failed_job is not None
    claimed = acquire_job_by_id(job_id=failed_job.id, worker_id="audit-generation-failure")
    assert claimed is not None
    WorkerCommand._execute(claimed.id, claimed.lease_token)
    failed_job.refresh_from_db()
    assert failed_job.status == ProcessingJob.Status.FAILED
    replay, replay_created = enqueue_document_generation(
        document=failed_document,
        actor=inspector_a,
        expected_revision=failed_document.revision,
        idempotency_key="audit-generation-failure",
    )
    assert replay == failed_job
    assert replay_created is False
    failure = DomainChangeHistory.objects.get(
        object_id=failed_document.id,
        action="generation_failed",
    )
    _assert_detailed_history(failure, actor=inspector_a, reason="文书生成失败")
    assert (
        DomainChangeHistory.objects.filter(
            object_id=failed_document.id, action="generation_requested"
        ).count()
        == 1
    )
    assert (
        AuditEvent.objects.filter(object_id=failed_document.id, action="generation_failed").count()
        == 1
    )


def test_export_history_audit_and_lifecycle_revision_are_idempotent(
    document_factory, inspector_a
) -> None:
    document = document_factory(InspectionDocument.Type.RECORD)
    generated = _make_generated(document, inspector_a)
    document = confirm_document(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
    )

    first, first_payload = export_document(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
    )
    second, second_payload = export_document(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
    )
    assert second.id == first.id
    assert second_payload == first_payload
    assert first.document_revision_id == generated.id
    history = DomainChangeHistory.objects.get(object_id=document.id, action="exported")
    _assert_detailed_history(history, actor=inspector_a, reason="已导出当前确认文书")
    event = AuditEvent.objects.get(object_id=document.id, action="exported")
    _assert_minimized_audit(event)
    assert (
        DocumentRevision.objects.filter(
            document=document,
            event_type=DocumentRevision.EventType.EXPORTED,
        ).count()
        == 1
    )


def test_fact_dependency_and_photo_mutations_have_detailed_minimized_history(
    document_factory, inspector_a
) -> None:
    document = document_factory(InspectionDocument.Type.PHOTO)
    fact = Fact.objects.create(
        inspection=document.inspection,
        content=SECRET_TEXT,
        review_state=Fact.ReviewState.CONFIRMED,
        origin=Fact.Origin.MANUAL,
        confirmed_by=inspector_a,
        confirmed_at=timezone.now(),
        created_by=inspector_a,
    )
    document = replace_fact_dependencies(
        document=document,
        fact_ids=[fact.id],
        actor=inspector_a,
        expected_revision=document.revision,
    )
    material = Material(
        inspection=document.inspection,
        kind=Material.Kind.IMAGE,
        display_name=f"{SECRET_TEXT}.png",
        mime_type="image/png",
        extension=".png",
        sha256="a" * 64,
        byte_size=5,
        width=10,
        height=10,
        created_by=inspector_a,
    )
    material.original_file.save("original", ContentFile(b"image"), save=False)
    material.save()
    document = add_photo_sources(
        document=document,
        source_ids=[material.id],
        actor=inspector_a,
        expected_revision=document.revision,
    )
    added_revision = document.revision
    document = add_photo_sources(
        document=document,
        source_ids=[material.id],
        actor=inspector_a,
        expected_revision=document.revision,
    )
    assert document.revision == added_revision
    item = document.photo_items.get()
    item = update_photo_item(
        document=document,
        item=item,
        values={"description": SECRET_TEXT, "reflected_content": "13800000000"},
        actor=inspector_a,
        expected_revision=document.revision,
    )
    document.refresh_from_db()
    delete_photo_item(
        document=document,
        item=item,
        actor=inspector_a,
        expected_revision=document.revision,
    )

    actions = {
        "fact_dependencies_replaced",
        "photo_sources_added",
        "photo_item_updated",
        "photo_item_deleted",
    }
    histories = DomainChangeHistory.objects.filter(object_id=document.id, action__in=actions)
    assert set(histories.values_list("action", flat=True)) == actions
    for history in histories:
        assert history.actor == inspector_a
        assert history.created_at is not None
        assert history.old_values
        assert history.new_values
        assert history.reason
    events = AuditEvent.objects.filter(object_id=document.id, action__in=actions)
    assert set(events.values_list("action", flat=True)) == actions
    for event in events:
        _assert_minimized_audit(event)
    assert (
        DomainChangeHistory.objects.filter(
            object_id=document.id, action="photo_sources_added"
        ).count()
        == 1
    )


def test_qa_sync_add_edit_reorder_delete_have_detailed_minimized_history(
    document_factory, inspector_a
) -> None:
    document = document_factory(InspectionDocument.Type.INTERVIEW)
    material = Material(
        inspection=document.inspection,
        kind=Material.Kind.VIDEO,
        display_name="qa-source.mp4",
        mime_type="video/mp4",
        extension=".mp4",
        sha256="b" * 64,
        byte_size=5,
        duration_ms=10_000,
        created_by=inspector_a,
    )
    material.original_file.save("original", ContentFile(b"video"), save=False)
    material.save()
    transcript = Transcript.objects.create(
        material=material,
        full_text=SECRET_TEXT,
        updated_by=inspector_a,
    )
    TranscriptSegmentRecord.objects.create(
        transcript=transcript,
        sequence=1,
        start_ms=0,
        end_ms=5_000,
        text=SECRET_TEXT,
        suggested_role=TranscriptSegmentRecord.SemanticRole.QUESTION,
    )
    document = sync_qa_candidates(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
    )
    document = create_qa_item(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
        text="13800000000",
        confirmed_role="answer",
    )
    first = document.qa_items.order_by("sequence").first()
    assert first is not None
    update_qa_item(
        document=document,
        item=first,
        values={"text": f"{SECRET_TEXT}-edited", "confirmed_role": "question"},
        actor=inspector_a,
        expected_revision=document.revision,
    )
    document.refresh_from_db()
    ordered = list(document.qa_items.order_by("sequence").values_list("id", flat=True))
    document = reorder_qa_items(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
        item_ids=list(reversed(ordered)),
    )
    reordered_revision = document.revision
    document = reorder_qa_items(
        document=document,
        actor=inspector_a,
        expected_revision=document.revision,
        item_ids=list(reversed(ordered)),
    )
    assert document.revision == reordered_revision
    removed = document.qa_items.order_by("sequence").first()
    assert removed is not None
    delete_qa_item(
        document=document,
        item=removed,
        actor=inspector_a,
        expected_revision=document.revision,
    )

    actions = {
        "qa_candidates_synced",
        "qa_item_added",
        "qa_item_updated",
        "qa_items_reordered",
        "qa_item_deleted",
    }
    histories = DomainChangeHistory.objects.filter(object_id=document.id, action__in=actions)
    assert set(histories.values_list("action", flat=True)) == actions
    for history in histories:
        assert history.actor == inspector_a
        assert history.created_at is not None
        assert history.old_values
        assert history.new_values
        assert history.reason
    events = AuditEvent.objects.filter(object_id=document.id, action__in=actions)
    assert set(events.values_list("action", flat=True)) == actions
    for event in events:
        _assert_minimized_audit(event)
    assert (
        DomainChangeHistory.objects.filter(
            object_id=document.id, action="qa_items_reordered"
        ).count()
        == 1
    )
