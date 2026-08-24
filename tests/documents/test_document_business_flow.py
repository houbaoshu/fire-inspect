from __future__ import annotations

from datetime import datetime

import pytest
from django.utils import timezone
from rest_framework import status

from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment
from fire_inspect.documents.models import (
    DocumentFactDependency,
    DocumentRevision,
    InspectionDocument,
)
from fire_inspect.documents.services import RenderedDocument
from fire_inspect.facts.models import Fact
from fire_inspect.facts.services import create_manual_fact, edit_fact, transition_fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job
from fire_inspect.templates.services import initialize_authoritative_templates
from fire_inspect.transcripts.services import edit_segment, publish_transcript_output
from tests.domain_fixtures import create_media_material

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


def _create_document(client, inspection, document_type: str) -> dict:
    response = client.post(
        f"/api/v1/inspections/{inspection.id}/documents/",
        {"document_type": document_type},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"create-{document_type}",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return response.json()


def _confirmed_fact(*, inspection, actor, content: str, material=None) -> Fact:
    evidence = []
    if material is not None:
        evidence.append(
            {
                "material_id": str(material.id),
                "start_ms": 0,
                "end_ms": material.duration_ms,
            }
        )
    fact = create_manual_fact(
        inspection=inspection,
        actor=actor,
        content=content,
        evidence=evidence,
    )
    return transition_fact(
        fact_id=fact.id,
        actor=actor,
        expected_revision=fact.revision,
        action="confirmed",
    )


def _successful_render(payload: bytes, *, document_type: str) -> RenderedDocument:
    return RenderedDocument(
        payload=payload,
        page_count=1,
        structure_check_result={"passed": True, "document_type": document_type},
        render_check_result={"passed": True, "font_fidelity": True},
    )


def _run_document_job(document_id) -> ProcessingJob:
    job = acquire_job(
        worker_id="document-business-flow",
        task_types=[ProcessingJob.TaskType.DOCUMENT_GENERATION],
    )
    assert job is not None
    assert job.scope_id == document_id
    WorkerCommand._execute(job.id, job.lease_token)
    job.refresh_from_db()
    return job


def test_fixed_llm_generates_candidate_fields_from_confirmed_facts_and_preserves_manual(
    monkeypatch,
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
) -> None:
    monkeypatch.setenv("FIRE_INSPECT_LLM_PROVIDER", "fixed")
    monkeypatch.setenv("FIRE_INSPECT_ALLOW_FIXED_AI", "1")
    monkeypatch.setenv("FIRE_INSPECT_ENV", "acceptance")
    monkeypatch.setattr(
        "fire_inspect.documents.services.render_generated_document",
        _successful_render,
    )
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    fact = _confirmed_fact(
        inspection=inspection,
        actor=inspector_a,
        content="安全出口附近存在堆放物品。",
        material=material,
    )
    client = authenticated_client(inspector_a)
    document = _create_document(client, inspection, "record")

    assert document["fact_dependency_ids"] == [str(fact.id)]
    manual = client.patch(
        f"/api/v1/documents/{document['id']}/fields/record_number/",
        {"value": "海消检〔2026〕18号", "origin": "manual", "manual_locked": True},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert manual.status_code == status.HTTP_200_OK, manual.data
    revision = manual.json()["document_revision"]

    submitted = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{revision}"',
        HTTP_IDEMPOTENCY_KEY="generate-record-with-fixed-draft",
    )
    assert submitted.status_code == status.HTTP_202_ACCEPTED, submitted.data
    job = _run_document_job(document_id=InspectionDocument.objects.get(pk=document["id"]).id)
    assert job.status == ProcessingJob.Status.SUCCEEDED, job.safe_error_message

    generated = client.get(f"/api/v1/documents/{document['id']}/").json()
    fields = {item["key"]: item for item in generated["fields"]}
    assert fields["record_number"] == {
        **fields["record_number"],
        "value": "海消检〔2026〕18号",
        "origin": "manual",
        "manual_locked": True,
    }
    assert "安全出口附近存在堆放物品" in fields["inspection_content"]["value"]
    assert fields["inspection_content"]["origin"] == "ai"
    assert fields["inspection_content"]["manual_locked"] is False
    dependency = DocumentFactDependency.objects.get(document_id=document["id"])
    assert dependency.fact_id == fact.id
    assert dependency.fact_revision == fact.revision
    revision_record = DocumentRevision.objects.get(document_id=document["id"])
    assert revision_record.input_revision_vector["fact_revisions"] == {str(fact.id): fact.revision}
    attempt = job.attempts.get()
    assert attempt.capability == "llm"
    assert attempt.provider_name == "FixedResponseProvider"


def test_disabled_llm_fails_generation_without_publishing_or_overwriting_manual_content(
    monkeypatch,
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
) -> None:
    monkeypatch.setenv("FIRE_INSPECT_LLM_PROVIDER", "disabled")
    render_called = False

    def unexpected_render(payload: bytes, *, document_type: str):
        del payload, document_type
        nonlocal render_called
        render_called = True
        raise AssertionError("disabled provider must fail before DOCX rendering")

    monkeypatch.setattr(
        "fire_inspect.documents.services.render_generated_document",
        unexpected_render,
    )
    client = authenticated_client(inspector_a)
    document = _create_document(client, inspection, "record")
    for key, value in (
        ("record_number", "人工编号"),
        ("inspection_content", "人工整理内容"),
    ):
        response = client.patch(
            f"/api/v1/documents/{document['id']}/fields/{key}/",
            {"value": value, "origin": "manual", "manual_locked": True},
            format="json",
            HTTP_IF_MATCH=f'"{document["revision"]}"',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        document["revision"] = response.json()["document_revision"]

    submitted = client.post(
        f"/api/v1/documents/{document['id']}/generate/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
        HTTP_IDEMPOTENCY_KEY="generate-record-disabled-llm",
    )
    assert submitted.status_code == status.HTTP_202_ACCEPTED, submitted.data
    job = _run_document_job(document_id=InspectionDocument.objects.get(pk=document["id"]).id)
    assert job.status == ProcessingJob.Status.FAILED
    assert job.safe_error_code == "capability_unavailable"
    assert "人工" in job.safe_error_message
    assert render_called is False
    assert not DocumentRevision.objects.filter(document_id=document["id"]).exists()

    current = client.get(f"/api/v1/documents/{document['id']}/").json()
    fields = {item["key"]: item for item in current["fields"]}
    assert fields["record_number"]["value"] == "人工编号"
    assert fields["inspection_content"]["value"] == "人工整理内容"
    assert all(fields[key]["manual_locked"] for key in fields)
    assert current["generator_status"] == "failed"
    assert current["generation_valid"] is False


def test_document_creation_and_dependency_api_default_to_confirmed_facts_and_invalidate_exactly(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
) -> None:
    first = _confirmed_fact(
        inspection=inspection,
        actor=inspector_a,
        content="第一条已确认事实。",
    )
    second = _confirmed_fact(
        inspection=inspection,
        actor=inspector_a,
        content="第二条已确认事实。",
    )
    Fact.objects.create(
        inspection=inspection,
        content="尚未确认候选",
        origin=Fact.Origin.MANUAL,
        created_by=inspector_a,
    )
    client = authenticated_client(inspector_a)
    record = _create_document(client, inspection, "record")
    photo = _create_document(client, inspection, "photo")
    assert set(record["fact_dependency_ids"]) == {str(first.id), str(second.id)}
    assert set(photo["fact_dependency_ids"]) == {str(first.id), str(second.id)}

    adjusted = client.put(
        f"/api/v1/documents/{record['id']}/fact-dependencies/",
        {"fact_ids": [str(first.id)]},
        format="json",
        HTTP_IF_MATCH=f'"{record["revision"]}"',
    )
    assert adjusted.status_code == status.HTTP_200_OK, adjusted.data
    assert adjusted.json()["fact_dependency_ids"] == [str(first.id)]
    listed = client.get(f"/api/v1/documents/{record['id']}/fact-dependencies/")
    assert listed.status_code == status.HTTP_200_OK
    assert listed.json()["facts"] == [
        {
            "id": str(first.id),
            "content": first.content,
            "revision": first.revision,
            "needs_verification": False,
        }
    ]

    InspectionDocument.objects.filter(pk__in=[record["id"], photo["id"]]).update(
        state=InspectionDocument.State.CONFIRMED
    )
    edit_fact(
        fact_id=second.id,
        actor=inspector_a,
        expected_revision=second.revision,
        values={"content": "第二条事实修订。"},
    )
    assert InspectionDocument.objects.get(pk=record["id"]).state == "confirmed"
    assert InspectionDocument.objects.get(pk=photo["id"]).state == "draft"


def test_interview_qa_candidates_can_sync_after_transcript_arrives_and_be_managed_via_api(
    authenticated_client,
    inspector_a,
    inspection,
    authoritative_templates,
) -> None:
    client = authenticated_client(inspector_a)
    document = _create_document(client, inspection, "interview")
    assert document["qa_items"] == []

    material = create_media_material(inspection=inspection, created_by=inspector_a)
    transcript, _ = publish_transcript_output(
        TranscriptOutput(
            full_text="问：现场情况如何？\n答：大厅存在堆放物品。",
            segments=(
                TranscriptSegment(
                    material.id,
                    0,
                    4_000,
                    "现场情况如何？",
                    SemanticRole.QUESTION,
                ),
                TranscriptSegment(
                    material.id,
                    4_000,
                    10_000,
                    "大厅存在堆放物品。",
                    SemanticRole.ANSWER,
                ),
            ),
        ),
        updated_by=inspector_a,
    )
    synced = client.post(
        f"/api/v1/documents/{document['id']}/qa-items/sync/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert synced.status_code == status.HTTP_200_OK, synced.data
    document = synced.json()
    assert [item["sequence"] for item in document["qa_items"]] == [1, 2]
    assert [item["suggested_role"] for item in document["qa_items"]] == [
        "question",
        "answer",
    ]

    first = document["qa_items"][0]
    protected = client.patch(
        f"/api/v1/documents/{document['id']}/qa-items/{first['id']}/",
        {"text": "人工确认后的第一个问题。", "confirmed_role": "question"},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert protected.status_code == status.HTTP_200_OK, protected.data
    document = protected.json()
    source = transcript.segments.order_by("sequence").first()
    edit_segment(
        transcript_id=transcript.id,
        segment_id=source.id,
        actor=inspector_a,
        expected_revision=transcript.revision,
        text="稍后到达的转写修订不得覆盖已确认问答。",
        confirmed_role="answer",
    )
    resynced = client.post(
        f"/api/v1/documents/{document['id']}/qa-items/sync/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert resynced.status_code == status.HTTP_200_OK, resynced.data
    document = resynced.json()
    preserved = next(item for item in document["qa_items"] if item["id"] == first["id"])
    assert preserved["text"] == "人工确认后的第一个问题。"
    assert preserved["confirmed_role"] == "question"
    assert preserved["content_manual_locked"] is True

    added = client.post(
        f"/api/v1/documents/{document['id']}/qa-items/",
        {"text": "人工补充回答。", "confirmed_role": "answer"},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert added.status_code == status.HTTP_201_CREATED, added.data
    document = added.json()
    manual_id = document["qa_items"][-1]["id"]

    reordered_ids = [manual_id, *[item["id"] for item in document["qa_items"][:-1]]]
    reordered = client.put(
        f"/api/v1/documents/{document['id']}/qa-items/",
        {"item_ids": reordered_ids},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert reordered.status_code == status.HTTP_200_OK, reordered.data
    document = reordered.json()
    assert [item["id"] for item in document["qa_items"]] == reordered_ids
    assert [item["sequence"] for item in document["qa_items"]] == [1, 2, 3]

    removed = client.delete(
        f"/api/v1/documents/{document['id']}/qa-items/{manual_id}/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    assert removed.status_code == status.HTTP_200_OK, removed.data
    assert manual_id not in {item["id"] for item in removed.json()["qa_items"]}
