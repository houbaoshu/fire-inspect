from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from django.core.management import call_command

from fire_inspect.ai.providers import FixedResponseProvider, ProviderFailure
from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job, enqueue_job, fail_job, retry_job
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.transcripts.models import Transcript
from fire_inspect.transcripts.services import publish_transcript_output
from tests.domain_fixtures import create_media_material
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("capability", ("asr", "vision", "llm", "embedding"))
def test_ai_capability_failure_is_local_and_does_not_disable_healthy_capabilities(
    capability, tmp_path
) -> None:
    provider = FixedResponseProvider(fail_capabilities=frozenset({capability}))
    media = tmp_path / "material.bin"
    media.write_bytes(b"fixture")
    with pytest.raises(ProviderFailure, match="单独重试"):
        if capability == "asr":
            provider.transcribe(uuid4(), media, 1_000)
        elif capability == "vision":
            provider.describe(uuid4(), [(0, media)])
        elif capability == "embedding":
            provider.embed(["可用文本"])
        else:
            material_id = uuid4()
            transcript = __import__(
                "fire_inspect.ai.schemas", fromlist=["TranscriptOutput"]
            ).TranscriptOutput(
                "候选事实",
                (
                    __import__(
                        "fire_inspect.ai.schemas", fromlist=["TranscriptSegment"]
                    ).TranscriptSegment(material_id, 0, 1_000, "候选事实"),
                ),
            )
            provider.extract_facts(transcript, ())

    if capability == "llm":
        assert provider.embed(["仍可生成向量"])
    else:
        assert provider.answer("仍可人工查询", {"inspection_id": str(uuid4())}).text


@pytest.mark.parametrize(
    "task_type",
    (
        ProcessingJob.TaskType.ASR,
        ProcessingJob.TaskType.VISION,
        ProcessingJob.TaskType.FACT_EXTRACTION,
        ProcessingJob.TaskType.EMBEDDING,
        ProcessingJob.TaskType.DOCUMENT_GENERATION,
    ),
)
def test_failed_step_preserves_original_other_results_and_retries_only_same_job(
    inspector_a, settings, task_type
) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="manual.txt",
        text="人工内容必须保留",
    )
    original_path = Path(material.original_file.path)
    successful_key = f"derived/materials/{material.id}/1/previous/result.txt"
    successful_path = Path(settings.MEDIA_ROOT) / successful_key
    successful_path.parent.mkdir(parents=True, exist_ok=True)
    successful_path.write_text("之前成功的结果", encoding="utf-8")
    derivative = MaterialDerivative.objects.create(
        material=material,
        kind=MaterialDerivative.Kind.PREVIEW,
        storage_key=successful_key,
        mime_type="text/plain",
        sha256="a" * 64,
        byte_size=successful_path.stat().st_size,
    )
    job = enqueue_job(
        task_type=task_type,
        scope_type="material",
        scope_id=material.id,
        inspection=inspection,
        input_fingerprint=f"failure-{task_type}",
        idempotency_key=str(uuid4()),
        created_by=inspector_a,
        expected_scope_revision=material.revision,
    )[0]
    leased = acquire_job(worker_id="failure-test", task_types=[task_type])
    assert leased and leased.id == job.id
    fail_job(
        job_id=leased.id,
        lease_token=leased.lease_token,
        safe_error_code="provider_unavailable",
        safe_error_message="服务暂不可用，可恢复后单独重试。",
    )

    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.FAILED
    assert job.safe_error_message == "服务暂不可用，可恢复后单独重试。"
    assert original_path.read_text(encoding="utf-8") == "人工内容必须保留"
    assert successful_path.read_text(encoding="utf-8") == "之前成功的结果"
    assert MaterialDerivative.objects.filter(pk=derivative.pk).exists()

    retried = retry_job(job_id=job.id, actor=inspector_a, idempotency_key=str(uuid4()))
    assert retried.id == job.id
    assert retried.status == ProcessingJob.Status.QUEUED
    assert ProcessingJob.objects.filter(scope_id=material.id, task_type=task_type).count() == 1


def test_failed_job_public_error_is_redacted_at_persistence_boundary(
    authenticated_client, inspector_a, settings
) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="manual.txt",
        text="人工内容",
    )
    job = enqueue_job(
        task_type=ProcessingJob.TaskType.ASR,
        scope_type="material",
        scope_id=material.id,
        inspection=inspection,
        input_fingerprint="redaction-failure",
        idempotency_key=str(uuid4()),
        created_by=inspector_a,
    )[0]
    leased = acquire_job(worker_id="redaction-test", task_types=[ProcessingJob.TaskType.ASR])
    canary = (
        f"api_key=CANARY_AI_SECRET sessionid=CANARY_SESSION "
        f"13812345678 {settings.FIRE_INSPECT_STAGING_ROOT}/partial.bin"
    )
    fail_job(
        job_id=leased.id,
        lease_token=leased.lease_token,
        safe_error_code="provider_failed",
        safe_error_message=canary,
    )
    response = authenticated_client(inspector_a).get(f"/api/v1/jobs/{job.id}")
    assert response.status_code == 200
    payload = str(response.json())
    for forbidden in (
        "CANARY_AI_SECRET",
        "CANARY_SESSION",
        "13812345678",
        str(settings.FIRE_INSPECT_STAGING_ROOT),
    ):
        assert forbidden not in payload


@pytest.mark.parametrize(
    ("task_type", "failed_capability"),
    (
        (ProcessingJob.TaskType.ASR, "asr"),
        (ProcessingJob.TaskType.VISION, "vision"),
        (ProcessingJob.TaskType.FACT_EXTRACTION, "llm"),
    ),
)
@pytest.mark.django_db(transaction=True)
def test_real_ai_handlers_fail_without_partial_publish_and_same_job_recovers(
    settings,
    inspector_a,
    monkeypatch,
    task_type,
    failed_capability,
) -> None:
    from fire_inspect.ai import handlers

    inspection = create_inspection(inspector_a)
    material = create_media_material(inspection=inspection, created_by=inspector_a)
    original_path = Path(material.original_file.path)
    original_bytes = original_path.read_bytes()

    if task_type == ProcessingJob.TaskType.VISION:
        frame_path = Path(settings.MEDIA_ROOT) / f"derived/materials/{material.id}/frame.jpg"
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        frame_path.write_bytes(b"safe-keyframe")
        MaterialDerivative.objects.create(
            material=material,
            kind=MaterialDerivative.Kind.KEYFRAME,
            storage_key=frame_path.relative_to(settings.MEDIA_ROOT).as_posix(),
            mime_type="image/jpeg",
            sha256="b" * 64,
            byte_size=frame_path.stat().st_size,
            source_timestamp_ms=5_000,
        )
    elif task_type == ProcessingJob.TaskType.FACT_EXTRACTION:
        publish_transcript_output(
            TranscriptOutput(
                full_text="问：现场如何？\n答：现场情况需要人工核实。",
                segments=(
                    TranscriptSegment(
                        material.id,
                        0,
                        5_000,
                        "问：现场如何？",
                        SemanticRole.QUESTION,
                    ),
                    TranscriptSegment(
                        material.id,
                        5_000,
                        material.duration_ms,
                        "答：现场情况需要人工核实。",
                        SemanticRole.ANSWER,
                    ),
                ),
            ),
            updated_by=inspector_a,
        )

    job, _ = enqueue_job(
        task_type=task_type,
        scope_type="material",
        scope_id=material.id,
        inspection=inspection,
        input_fingerprint=f"handler-failure-{task_type}",
        idempotency_key=str(uuid4()),
        created_by=inspector_a,
        expected_scope_revision=material.revision,
    )
    monkeypatch.setattr(
        handlers,
        "provider_for",
        lambda capability: FixedResponseProvider(fail_capabilities=frozenset({failed_capability})),
    )
    call_command("runworker", once=True, concurrency=1)

    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.FAILED
    assert job.safe_error_code == "provider_or_processing_failed"
    assert failed_capability in job.safe_error_message
    assert "单独重试" in job.safe_error_message
    assert original_path.read_bytes() == original_bytes
    if task_type == ProcessingJob.TaskType.ASR:
        assert not Transcript.objects.filter(material=material).exists()
    elif task_type == ProcessingJob.TaskType.VISION:
        assert not material.visual_analyses.exists()
        assert material.derivatives.filter(kind=MaterialDerivative.Kind.KEYFRAME).exists()
    else:
        assert material.transcript.full_text == "问：现场如何？\n答：现场情况需要人工核实。"
        assert not inspection.facts.exists()

    retried = retry_job(job_id=job.id, actor=inspector_a, idempotency_key=str(uuid4()))
    assert retried.id == job.id
    monkeypatch.setattr(handlers, "provider_for", lambda capability: FixedResponseProvider())
    call_command("runworker", once=True, concurrency=1)

    job.refresh_from_db()
    assert job.status == ProcessingJob.Status.SUCCEEDED
    assert job.attempt_count == 2
    assert original_path.read_bytes() == original_bytes
    if task_type == ProcessingJob.TaskType.ASR:
        assert Transcript.objects.filter(material=material).exists()
    elif task_type == ProcessingJob.TaskType.VISION:
        assert material.visual_analyses.filter(input_revision=material.revision).exists()
    else:
        assert inspection.facts.filter(primary_source_material=material).exists()
