from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from django.core.files import File
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APIClient

from fire_inspect.documents.inspection import assert_document_structure
from fire_inspect.documents.models import DocumentFactDependency, InspectionDocument
from fire_inspect.documents.services import RenderedDocument
from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job
from fire_inspect.knowledge.django_models import (
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from fire_inspect.materials.models import MaterialDerivative
from fire_inspect.templates.services import initialize_authoritative_templates

ROOT = Path(__file__).resolve().parents[2]
TEST_PASSWORD = "Strong-Test-Password-42!"

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_acceptance]


def _assert_status(response, expected: int):
    assert response.status_code == expected, getattr(response, "data", None)
    return response


def _login(client: APIClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login/",
        {"username": username, "password": TEST_PASSWORD},
        format="json",
    )
    _assert_status(response, status.HTTP_200_OK)
    assert response.json()["user"]["username"] == username


def _upload_video(client: APIClient, inspection_id: str, source: Path) -> dict:
    with source.open("rb") as stream:
        response = client.post(
            f"/api/v1/inspections/{inspection_id}/materials/",
            {"file": File(stream, name=source.name)},
            format="multipart",
        )
    _assert_status(response, status.HTTP_201_CREATED)
    payload = response.json()
    assert payload["display_name"] == source.name
    assert payload["kind"] == "video"
    assert payload["duration_ms"] > 0
    return payload


def _run_material_phase(
    client: APIClient,
    materials: Iterable[dict],
    step: str,
) -> None:
    submitted: list[str] = []
    for material in materials:
        response = client.post(
            f"/api/v1/materials/{material['id']}/jobs/{step}/",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"fixed-e2e-{material['id']}-{step}",
        )
        _assert_status(response, status.HTTP_202_ACCEPTED)
        submitted.append(response.json()["id"])

    # Claim both slots before either completes. The PostgreSQL gate separately
    # exercises truly simultaneous SKIP LOCKED claims; here we keep SQLite
    # deterministic while still proving that two in-flight materials never mix.
    claimed = [
        acquire_job(worker_id=f"fixed-e2e-{step}-{slot}", task_types=[step]) for slot in range(2)
    ]
    assert all(claimed)
    assert {str(job.id) for job in claimed if job is not None} == set(submitted)
    assert len({job.scope_id for job in claimed if job is not None}) == 2

    for job in claimed:
        assert job is not None
        WorkerCommand._execute(job.id, job.lease_token)

    results = ProcessingJob.objects.filter(id__in=submitted)
    assert results.count() == 2
    assert set(results.values_list("status", flat=True)) == {ProcessingJob.Status.SUCCEEDED}, list(
        results.values("task_type", "safe_error_code", "safe_error_message")
    )


def _review_transcripts(client: APIClient, inspection_id: str) -> list[dict]:
    response = client.get(f"/api/v1/inspections/{inspection_id}/transcripts/")
    _assert_status(response, status.HTTP_200_OK)
    transcripts = response.json()
    assert len(transcripts) == 2
    for transcript in transcripts:
        original_text = transcript["full_text"]
        response = client.patch(
            f"/api/v1/transcripts/{transcript['id']}/",
            {"full_text": f"{original_text}\n（人工已核对）"},
            format="json",
            HTTP_IF_MATCH=f'"{transcript["revision"]}"',
        )
        _assert_status(response, status.HTTP_200_OK)
        revision = response.json()["revision"]
        for sequence, segment in enumerate(transcript["segments"]):
            role = "question" if sequence % 2 == 0 else "answer"
            response = client.patch(
                f"/api/v1/transcripts/{transcript['id']}/segments/{segment['id']}/",
                {"text": segment["text"], "confirmed_role": role},
                format="json",
                HTTP_IF_MATCH=f'"{revision}"',
            )
            _assert_status(response, status.HTTP_200_OK)
            assert response.json()["confirmed_role"] == role
            revision = response.json()["transcript_revision"]
        history = client.get(f"/api/v1/transcripts/{transcript['id']}/history/")
        _assert_status(history, status.HTTP_200_OK)
        assert history.json()
    return client.get(f"/api/v1/inspections/{inspection_id}/transcripts/").json()


def _confirm_and_link_facts(
    client: APIClient,
    inspection_id: str,
    material_ids: set[str],
) -> list[dict]:
    response = client.get(f"/api/v1/inspections/{inspection_id}/facts/")
    _assert_status(response, status.HTTP_200_OK)
    facts = response.json()
    assert len(facts) >= 4
    combined = "\n".join(item["content"] for item in facts)
    assert "消防控制室" in combined
    assert any(token in combined for token in ("不在岗", "休息"))
    assert "持证情况需要核实" in combined
    assert any(token in combined for token in ("大厅", "现场"))
    assert "堆放物品" in combined
    assert any(token in combined for token in ("仓库", "用途发生改变"))
    assert not any(
        token in combined
        for token in (
            "罚款",
            "处罚决定",
            "行政处罚",
            "责令停产",
            "证件编号",
            "身份证号",
            "张三",
            "李四",
        )
    )

    confirmed: list[dict] = []
    for fact in facts:
        assert fact["primary_source_material_id"] in material_ids
        media_ranges = [item for item in fact["evidence"] if item["type"] == "media_range"]
        assert media_ranges
        assert {item["material_id"] for item in media_ranges} == {
            fact["primary_source_material_id"]
        }
        response = client.post(
            f"/api/v1/inspections/{inspection_id}/facts/{fact['id']}/confirm/",
            {},
            format="json",
            HTTP_IF_MATCH=f'"{fact["revision"]}"',
        )
        _assert_status(response, status.HTTP_200_OK)
        confirmed.append(response.json())

    candidates_response = client.get(f"/api/v1/inspections/{inspection_id}/evidence-candidates/")
    _assert_status(candidates_response, status.HTTP_200_OK)
    candidates = candidates_response.json()
    for material_id in sorted(material_ids):
        fact = next(item for item in confirmed if item["primary_source_material_id"] == material_id)
        candidate = next(
            item
            for item in candidates
            if item["type"] == "keyframe" and item["material_id"] == material_id
        )
        response = client.post(
            f"/api/v1/inspections/{inspection_id}/facts/{fact['id']}/evidence/",
            {"id": candidate["id"]},
            format="json",
            HTTP_IF_MATCH=f'"{fact["revision"]}"',
            HTTP_IDEMPOTENCY_KEY=f"fixed-e2e-link-{candidate['id']}",
        )
        _assert_status(response, status.HTTP_200_OK)
        linked = response.json()
        keyframe = next(item for item in linked["evidence"] if item["type"] == "keyframe")
        assert keyframe["material_id"] == material_id
        assert abs(keyframe["start_ms"] - candidate["start_ms"]) <= 3_000
        confirmed[confirmed.index(fact)] = linked
    return confirmed


def _patch_field(client: APIClient, document: dict, key: str, value) -> dict:
    response = client.patch(
        f"/api/v1/documents/{document['id']}/fields/{key}/",
        {"value": value, "origin": "manual", "manual_locked": True},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
    )
    _assert_status(response, status.HTTP_200_OK)
    document["revision"] = response.json()["document_revision"]
    return document


def _document_command(client: APIClient, document: dict, command: str) -> dict:
    response = client.post(
        f"/api/v1/documents/{document['id']}/{command}/",
        {},
        format="json",
        HTTP_IF_MATCH=f'"{document["revision"]}"',
        HTTP_IDEMPOTENCY_KEY=f"fixed-e2e-{document['id']}-{command}-{document['revision']}",
    )
    expected_status = status.HTTP_202_ACCEPTED if command == "generate" else status.HTTP_200_OK
    assert response.status_code == expected_status, {
        "document_type": document["document_type"],
        "command": command,
        "response": getattr(response, "data", None),
    }
    result = response.json()
    if command != "generate":
        return result

    assert result["generator_status"] == "queued"
    assert result["generation_valid"] is False
    job = acquire_job(
        worker_id=f"fixed-e2e-document-{document['document_type']}",
        task_types=[ProcessingJob.TaskType.DOCUMENT_GENERATION],
    )
    assert job is not None
    assert str(job.id) == result["generator_job_id"]
    assert str(job.scope_id) == document["id"]
    WorkerCommand._execute(job.id, job.lease_token)
    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.SUCCEEDED, {
        "document_type": document["document_type"],
        "safe_error_code": job.safe_error_code,
        "safe_error_message": job.safe_error_message,
    }
    return _get_document(client, document["id"])


def _get_document(client: APIClient, document_id: str) -> dict:
    response = client.get(f"/api/v1/documents/{document_id}/")
    _assert_status(response, status.HTTP_200_OK)
    return response.json()


def _fixed_render_precheck(payload: bytes, *, document_type: str) -> RenderedDocument:
    structure = assert_document_structure(payload, expected_type=document_type)
    return RenderedDocument(
        payload=payload,
        page_count={"record": 1, "photo": 3, "interview": 1}[document_type],
        structure_check_result={
            "passed": True,
            "document_type": structure.document_type,
            "package_part_count": structure.package_part_count,
            "checks": list(structure.checks),
        },
        # This deterministic response belongs only to the fixed E2E. The
        # designated Windows gate remains the sole visual acceptance authority.
        render_check_result={
            "passed": True,
            "font_fidelity": True,
            "manual_review_required": False,
            "warnings": [],
        },
    )


def _create_and_prepare_documents(
    client: APIClient,
    inspection_id: str,
    facts: list[dict],
) -> dict[str, dict]:
    documents: dict[str, dict] = {}
    for document_type in ("record", "photo", "interview"):
        response = client.post(
            f"/api/v1/inspections/{inspection_id}/documents/",
            {"document_type": document_type},
            format="json",
            HTTP_IDEMPOTENCY_KEY=f"fixed-e2e-create-{document_type}",
        )
        _assert_status(response, status.HTTP_201_CREATED)
        documents[document_type] = response.json()

    source_ids = sorted({item["primary_source_material_id"] for item in facts})
    assert len(source_ids) == 2
    record_fact_ids = [
        item["id"] for item in facts if item["primary_source_material_id"] == source_ids[0]
    ]
    photo_fact_ids = [
        item["id"] for item in facts if item["primary_source_material_id"] == source_ids[1]
    ]
    assert record_fact_ids
    assert photo_fact_ids

    record = documents["record"]
    dependency_response = client.put(
        f"/api/v1/documents/{record['id']}/fact-dependencies/",
        {"fact_ids": record_fact_ids},
        format="json",
        HTTP_IF_MATCH=f'"{record["revision"]}"',
    )
    _assert_status(dependency_response, status.HTTP_200_OK)
    record = dependency_response.json()
    record = _patch_field(client, record, "record_number", "海消检〔2026〕18号")
    documents["record"] = record

    photo = documents["photo"]
    dependency_response = client.put(
        f"/api/v1/documents/{photo['id']}/fact-dependencies/",
        {"fact_ids": photo_fact_ids},
        format="json",
        HTTP_IF_MATCH=f'"{photo["revision"]}"',
    )
    _assert_status(dependency_response, status.HTTP_200_OK)
    photo = dependency_response.json()
    candidates_response = client.get(f"/api/v1/documents/{photo['id']}/photo-candidates/")
    _assert_status(candidates_response, status.HTTP_200_OK)
    candidates = [item for item in candidates_response.json() if item["kind"] == "keyframe"]
    assert len(candidates) >= 3
    response = client.post(
        f"/api/v1/documents/{photo['id']}/photo-items/",
        {"source_ids": [item["id"] for item in candidates[:3]]},
        format="json",
        HTTP_IF_MATCH=f'"{photo["revision"]}"',
    )
    _assert_status(response, status.HTTP_200_OK)
    photo = response.json()
    documents["photo"] = photo

    interview = documents["interview"]
    for key, value in (
        ("interviewee_name", "被询问人"),
        ("id_type", "居民身份证"),
        ("id_number", "000000000000000000"),
        ("contact", "13800000000"),
    ):
        interview = _patch_field(client, interview, key, value)
    assert interview["qa_items"], "询问文书应从本检查连续转写生成问答候选"
    assert all(item["confirmed_role"] in {"question", "answer"} for item in interview["qa_items"])
    first_qa = interview["qa_items"][0]
    response = client.patch(
        f"/api/v1/documents/{interview['id']}/qa-items/{first_qa['id']}/",
        {
            "text": f"{first_qa['text']}（人工确认）",
            "confirmed_role": first_qa["confirmed_role"],
        },
        format="json",
        HTTP_IF_MATCH=f'"{interview["revision"]}"',
    )
    _assert_status(response, status.HTTP_200_OK)
    interview = response.json()
    documents["interview"] = interview
    return documents


def _generate_confirm_export(client: APIClient, documents: dict[str, dict]) -> None:
    for document_type, document in documents.items():
        document = _document_command(client, document, "generate")
        assert document["generator_status"] == "succeeded"
        assert document["generation_valid"] is True
        fields = {item["key"]: item for item in document["fields"]}
        if document_type == "record":
            assert fields["inspection_content"]["value"]
            assert fields["inspection_content"]["origin"] == "ai"
            assert fields["inspection_content"]["manual_locked"] is False
        elif document_type == "photo":
            assert fields["shooting_location"]["value"]
            assert fields["shooting_location"]["origin"] == "ai"
            assert fields["photographer"]["value"] == "检查员甲"
            assert fields["photographer"]["origin"] == "ai"
            assert len(document["photo_items"]) == 3
            assert all(item["reflected_content"] for item in document["photo_items"])
            assert all(
                item["reflected_content_origin"] == "ai"
                and item["description_origin"] == "ai"
                and item["reflected_content_manual_locked"] is False
                and item["description_manual_locked"] is False
                for item in document["photo_items"]
            )
        else:
            assert fields["started_at"]["origin"] == "ai"
            assert fields["location"]["origin"] == "ai"
            assert fields["interviewee_name"]["origin"] == "manual"
            assert all(
                item["confirmed_role"] in {"question", "answer"} for item in document["qa_items"]
            )
            assert document["qa_items"][0]["content_origin"] == "manual"
            assert document["qa_items"][0]["content_manual_locked"] is True
        document = _document_command(client, document, "confirm")
        assert document["state"] == "confirmed"
        export = client.post(
            f"/api/v1/documents/{document['id']}/export/",
            {},
            format="json",
            HTTP_IF_MATCH=f'"{document["revision"]}"',
            HTTP_IDEMPOTENCY_KEY=f"fixed-e2e-export-{document['id']}-{document['revision']}",
        )
        _assert_status(export, status.HTTP_200_OK)
        assert export.content.startswith(b"PK")
        documents[document_type] = document


def test_fixed_ai_full_delivery_flow(
    monkeypatch,
    admin_user,
    inspector_a,
    inspector_b,
) -> None:
    for capability in ("asr", "vision", "llm", "embedding"):
        monkeypatch.setenv(f"FIRE_INSPECT_{capability.upper()}_PROVIDER", "fixed")
    monkeypatch.setenv("FIRE_INSPECT_ALLOW_FIXED_AI", "1")
    monkeypatch.setenv("FIRE_INSPECT_ENV", "acceptance")

    initialize_authoritative_templates(actor=admin_user)
    call_command("initialize_fire_law", verbosity=0)
    from fire_inspect.documents import services as document_services

    monkeypatch.setattr(
        document_services,
        "render_generated_document",
        _fixed_render_precheck,
    )
    admin = APIClient()
    _login(admin, admin_user.username)
    _assert_status(admin.get("/api/v1/admin/users/"), status.HTTP_200_OK)

    client = APIClient()
    _login(client, inspector_a.username)
    created = client.post(
        "/api/v1/inspections/",
        {
            "unit_name": "滨江商场",
            "unit_address": "海州市滨江路18号",
            "responsible_person": "负责人甲",
            "contact_name": "联系人乙",
            "contact_phone": "13800000000",
            "inspection_at": "2026-08-24T10:30:00+08:00",
            "inspection_type": "construction_site",
            "general_notes": "固定响应端到端验收",
            "personnel": [
                {
                    "name": "检查员甲",
                    "work_unit": "海州市消防救援支队",
                    "role": "inspector",
                }
            ],
        },
        format="json",
    )
    _assert_status(created, status.HTTP_201_CREATED)
    inspection = created.json()

    sources = [ROOT / "testdata/videos/1.mp4", ROOT / "testdata/videos/4.mp4"]
    assert all(source.is_file() for source in sources)
    materials = [_upload_video(client, inspection["id"], source) for source in sources]
    material_ids = {item["id"] for item in materials}

    for step in (
        "asr",
        "transcode",
        "audio_extract",
        "keyframes",
        "vision",
        "fact_extraction",
    ):
        _run_material_phase(client, materials, step)

    material_listing = client.get(f"/api/v1/inspections/{inspection['id']}/materials/")
    _assert_status(material_listing, status.HTTP_200_OK)
    processed = {item["display_name"]: item for item in material_listing.json()}
    assert processed["1.mp4"]["display_rotation_override"] == 0
    assert all(item["duration_ms"] > 0 for item in processed.values())
    for material in materials:
        material_id = material["id"]
        step_statuses = {
            step["name"]: step["status"] for step in processed[material["display_name"]]["steps"]
        }
        assert step_statuses == {
            "transcode": "succeeded",
            "audio_extract": "succeeded",
            "keyframes": "succeeded",
            "asr": "succeeded",
            "vision": "succeeded",
            "fact_extraction": "succeeded",
        }
        assert MaterialDerivative.objects.filter(
            material_id=material_id,
            kind=MaterialDerivative.Kind.PLAYBACK,
        ).exists()
        assert MaterialDerivative.objects.filter(
            material_id=material_id,
            kind=MaterialDerivative.Kind.AUDIO_EXTRACT,
        ).exists()
        playback = client.get(f"/api/v1/materials/{material['id']}/play/", HTTP_RANGE="bytes=0-31")
        _assert_status(playback, status.HTTP_206_PARTIAL_CONTENT)
        playback.close()

    transcripts = _review_transcripts(client, inspection["id"])
    assert {item["material_id"] for item in transcripts} == material_ids
    facts = _confirm_and_link_facts(client, inspection["id"], material_ids)

    knowledge_document = KnowledgeDocumentRecord.objects.get(
        title="中华人民共和国消防法（测试样本）"
    )
    parse_job = acquire_job(
        worker_id="fixed-e2e-knowledge-parse",
        task_types=[ProcessingJob.TaskType.KNOWLEDGE_PARSE],
    )
    assert parse_job is not None
    assert str(parse_job.scope_id) == str(knowledge_document.id)
    WorkerCommand._execute(parse_job.id, parse_job.lease_token)
    parse_job.refresh_from_db()
    assert parse_job.status == ProcessingJob.Status.SUCCEEDED, {
        "safe_error_code": parse_job.safe_error_code,
        "safe_error_message": parse_job.safe_error_message,
    }
    knowledge_document.refresh_from_db()
    submitted_embedding = admin.post(
        f"/api/v1/knowledge/documents/{knowledge_document.id}/embedding/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY="fixed-e2e-fire-law-embedding",
    )
    assert submitted_embedding.status_code in {
        status.HTTP_200_OK,
        status.HTTP_202_ACCEPTED,
    }, submitted_embedding.data
    embedding_job = acquire_job(worker_id="fixed-e2e-embedding", task_types=["embedding"])
    assert embedding_job is not None
    assert str(embedding_job.id) == submitted_embedding.json()["id"]
    WorkerCommand._execute(embedding_job.id, embedding_job.lease_token)
    embedding_job.refresh_from_db()
    assert embedding_job.status == ProcessingJob.Status.SUCCEEDED
    indexed = KnowledgeEmbeddingRecord.objects.filter(
        clause__document=knowledge_document,
        input_revision=knowledge_document.revision,
        status=KnowledgeEmbeddingRecord.Status.SUCCEEDED,
    )
    assert indexed.count() == 74
    assert set(indexed.values_list("dimensions", flat=True)) == {16}
    assert len(set(indexed.values_list("model_fingerprint", flat=True))) == 1
    search = client.get("/api/v1/knowledge/search/", {"q": "自动消防系统操作人员必须持证上岗"})
    _assert_status(search, status.HTTP_200_OK)
    assert search.json()["found"] is True
    assert search.json()["retrieval_mode"] == "hybrid"
    assert search.json()["embedding_status"] == "succeeded"
    assert search.json()["embedding_fallback"] is False
    assert any(item["clause_identifier"] == "第二十一条" for item in search.json()["results"])
    missing = client.get("/api/v1/knowledge/search/", {"q": "量子传送门的星际消防许可规则"})
    _assert_status(missing, status.HTTP_200_OK)
    assert missing.json()["found"] is False

    documents = _create_and_prepare_documents(client, inspection["id"], facts)
    _generate_confirm_export(client, documents)

    record = documents["record"]
    record = _patch_field(client, record, "record_number", "海消检〔2026〕19号")
    record = _document_command(client, record, "generate")
    locked_field = next(item for item in record["fields"] if item["key"] == "record_number")
    assert locked_field["value"] == "海消检〔2026〕19号"
    assert locked_field["origin"] == "manual"
    assert locked_field["manual_locked"] is True
    record = _document_command(client, record, "confirm")
    documents["record"] = record

    updated = client.patch(
        f"/api/v1/inspections/{inspection['id']}/",
        {"unit_address": "海州市滨江路20号"},
        format="json",
        HTTP_IF_MATCH=f'"{inspection["shared_revision"]}"',
    )
    _assert_status(updated, status.HTTP_200_OK)
    inspection = updated.json()
    for document_type, document in documents.items():
        document = _get_document(client, document["id"])
        assert document["state"] == "draft"
        assert document["invalidation_reason"]
        documents[document_type] = document
    _generate_confirm_export(client, documents)

    photo = documents["photo"]
    first_photo = photo["photo_items"][0]
    response = client.patch(
        f"/api/v1/documents/{photo['id']}/photo-items/{first_photo['id']}/",
        {"description": "照片说明二次人工核对"},
        format="json",
        HTTP_IF_MATCH=f'"{photo["revision"]}"',
    )
    _assert_status(response, status.HTTP_200_OK)
    documents["photo"] = response.json()
    assert documents["photo"]["state"] == "draft"
    assert _get_document(client, documents["record"]["id"])["state"] == "confirmed"
    assert _get_document(client, documents["interview"]["id"])["state"] == "confirmed"
    documents["photo"] = _document_command(client, documents["photo"], "generate")
    documents["photo"] = _document_command(client, documents["photo"], "confirm")

    interview = documents["interview"]
    qa = interview["qa_items"][0]
    response = client.patch(
        f"/api/v1/documents/{interview['id']}/qa-items/{qa['id']}/",
        {"text": f"{qa['text']}（再次核对）", "confirmed_role": qa["confirmed_role"]},
        format="json",
        HTTP_IF_MATCH=f'"{interview["revision"]}"',
    )
    _assert_status(response, status.HTTP_200_OK)
    documents["interview"] = response.json()
    assert documents["interview"]["state"] == "draft"
    assert _get_document(client, documents["record"]["id"])["state"] == "confirmed"
    assert _get_document(client, documents["photo"]["id"])["state"] == "confirmed"
    documents["interview"] = _document_command(client, documents["interview"], "generate")
    documents["interview"] = _document_command(client, documents["interview"], "confirm")

    dependency = DocumentFactDependency.objects.filter(
        document_id=documents["record"]["id"]
    ).first()
    assert dependency is not None
    record_dependency_ids = set(documents["record"]["fact_dependency_ids"])
    photo_dependency_ids = set(documents["photo"]["fact_dependency_ids"])
    assert record_dependency_ids
    assert photo_dependency_ids
    assert record_dependency_ids.isdisjoint(photo_dependency_ids)
    assert documents["interview"]["fact_dependency_ids"] == []
    dependent_document_ids = {
        str(value)
        for value in DocumentFactDependency.objects.filter(fact_id=dependency.fact_id).values_list(
            "document_id", flat=True
        )
    }
    assert dependent_document_ids == {documents["record"]["id"]}
    dependent_fact = next(item for item in facts if item["id"] == str(dependency.fact_id))
    current_fact = client.get(
        f"/api/v1/inspections/{inspection['id']}/facts/{dependent_fact['id']}/"
    ).json()
    response = client.patch(
        f"/api/v1/inspections/{inspection['id']}/facts/{dependent_fact['id']}/",
        {"content": f"{current_fact['content']}（人工复核后修订）"},
        format="json",
        HTTP_IF_MATCH=f'"{current_fact["revision"]}"',
    )
    _assert_status(response, status.HTTP_200_OK)
    assert _get_document(client, documents["record"]["id"])["state"] == "draft"
    assert _get_document(client, documents["photo"]["id"])["state"] == "confirmed"
    assert _get_document(client, documents["interview"]["id"])["state"] == "confirmed"

    assistant = client.post(
        "/api/v1/assistant/query",
        {
            "inspection_id": inspection["id"],
            "question": "请只读概括当前检查，并提示人工核验。",
            "route": f"/inspections/{inspection['id']}",
        },
        format="json",
    )
    _assert_status(assistant, status.HTTP_200_OK)
    assert assistant.json()["read_only"] is True
    _assert_status(client.get("/api/v1/admin/jobs/"), status.HTTP_403_FORBIDDEN)

    other = APIClient()
    _login(other, inspector_b.username)
    assert other.get("/api/v1/inspections/").json() == []
    hidden_paths = (
        f"/api/v1/inspections/{inspection['id']}/",
        f"/api/v1/materials/{materials[0]['id']}/download/",
        f"/api/v1/materials/{materials[0]['id']}/play/",
        f"/api/v1/transcripts/{transcripts[0]['id']}/",
        f"/api/v1/inspections/{inspection['id']}/facts/{facts[0]['id']}/",
        f"/api/v1/documents/{documents['record']['id']}/",
    )
    for path in hidden_paths:
        _assert_status(other.get(path), status.HTTP_404_NOT_FOUND)
    hidden_assistant = other.post(
        "/api/v1/assistant/query",
        {"inspection_id": inspection["id"], "question": "读取检查", "route": "/"},
        format="json",
    )
    _assert_status(hidden_assistant, status.HTTP_404_NOT_FOUND)

    assert (
        InspectionDocument.objects.filter(inspection_id=inspection["id"], state="confirmed").count()
        == 2
    )
