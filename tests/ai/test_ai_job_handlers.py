from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.utils import timezone

from fire_inspect.ai.providers import FixedResponseProvider, ProviderFailure
from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment
from fire_inspect.facts.models import Fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.transcripts.models import Transcript


def _inspection(owner) -> Inspection:
    return Inspection.objects.create(
        owner=owner,
        unit_name="固定响应测试单位",
        inspection_at=timezone.now(),
        inspection_type=Inspection.Type.OTHER,
    )


def _material(settings, owner) -> Material:
    inspection = _inspection(owner)
    material = Material.objects.create(
        inspection=inspection,
        kind=Material.Kind.VIDEO,
        display_name="fixture.mp4",
        mime_type="video/mp4",
        extension=".mp4",
        sha256="0" * 64,
        byte_size=12,
        duration_ms=10_000,
        width=640,
        height=360,
        created_by=owner,
    )
    path = Path(settings.MEDIA_ROOT) / (
        f"originals/inspections/{inspection.id}/{material.id}/original"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixed-media")
    material.original_file.name = path.relative_to(settings.MEDIA_ROOT).as_posix()
    material.save(update_fields=("original_file",))
    return material


def _job(material: Material, task_type: str, suffix: str = "") -> ProcessingJob:
    return ProcessingJob.objects.create(
        task_type=task_type,
        scope_type="material",
        scope_id=material.id,
        inspection=material.inspection,
        input_fingerprint=f"fingerprint-{task_type}-{suffix}",
        idempotency_key=f"idempotency-{task_type}-{suffix}",
        expected_scope_revision=material.revision,
        created_by=material.created_by,
        available_at=timezone.now(),
        status=ProcessingJob.Status.RUNNING,
        attempt_count=1,
        lease_owner="unit-test-worker",
        lease_token=f"lease-{uuid4()}",
        lease_expires_at=timezone.now() + timedelta(minutes=5),
    )


@pytest.mark.django_db
def test_asr_vision_and_fact_handlers_publish_traceable_results(
    settings, inspector_a, monkeypatch
) -> None:
    from fire_inspect.ai import handlers

    material = _material(settings, inspector_a)
    frame = Path(settings.MEDIA_ROOT) / "derived/frame.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpeg")
    MaterialDerivative.objects.create(
        material=material,
        kind=MaterialDerivative.Kind.KEYFRAME,
        storage_key=frame.relative_to(settings.MEDIA_ROOT).as_posix(),
        mime_type="image/jpeg",
        sha256="1" * 64,
        byte_size=4,
        source_timestamp_ms=5_000,
    )

    class SemanticFixedProvider(FixedResponseProvider):
        def transcribe(self, material_id, media_path, duration_ms):
            midpoint = duration_ms // 2
            return TranscriptOutput(
                full_text="消防控制室值班人员今天休息，目前不在岗，持证情况需要核实。",
                segments=(
                    TranscriptSegment(
                        material_id,
                        0,
                        midpoint,
                        "问：消防控制室谁值班？",
                        SemanticRole.QUESTION,
                    ),
                    TranscriptSegment(
                        material_id,
                        midpoint,
                        duration_ms,
                        "答：值班人员休息不在岗，持证情况需要核实。",
                        SemanticRole.ANSWER,
                    ),
                ),
            )

    monkeypatch.setattr(handlers, "provider_for", lambda capability: SemanticFixedProvider())

    handlers.asr_handler(_job(material, ProcessingJob.TaskType.ASR))
    transcript = Transcript.objects.get(material=material)
    assert transcript.full_text
    assert transcript.segments.first().start_ms == 0
    assert transcript.segments.last().end_ms == material.duration_ms

    handlers.vision_handler(_job(material, ProcessingJob.TaskType.VISION))
    analysis = material.visual_analyses.get(input_revision=material.revision)
    assert analysis.descriptions[0]["timestamp_ms"] == 5_000

    handlers.fact_extraction_handler(_job(material, ProcessingJob.TaskType.FACT_EXTRACTION))
    fact = Fact.objects.get(primary_source_material=material, needs_verification=True)
    assert fact.needs_verification is True
    evidence = fact.media_ranges.get()
    assert evidence.material_id == material.id
    assert evidence.end_ms == material.duration_ms


@pytest.mark.django_db
def test_ai_handler_failure_never_deletes_original_or_previous_manual_content(
    settings, inspector_a, monkeypatch
) -> None:
    from fire_inspect.ai import handlers

    material = _material(settings, inspector_a)
    previous = Transcript.objects.create(
        material=material,
        full_text="人工保留内容",
        full_text_origin=Transcript.TextOrigin.MANUAL,
        updated_by=inspector_a,
    )

    class FailingProvider:
        def transcribe(self, *args, **kwargs):
            raise ProviderFailure("ASR 服务超时，可稍后单独重试")

    monkeypatch.setattr(handlers, "provider_for", lambda capability: FailingProvider())
    with pytest.raises(ProviderFailure, match="超时"):
        handlers.asr_handler(_job(material, ProcessingJob.TaskType.ASR, "failure"))
    assert Path(material.original_file.path).read_bytes() == b"fixed-media"
    previous.refresh_from_db()
    assert previous.full_text == "人工保留内容"


@pytest.mark.django_db
def test_image_vision_uses_the_original_image_without_requiring_keyframes(
    settings, inspector_a, monkeypatch
) -> None:
    from fire_inspect.ai import handlers

    inspection = _inspection(inspector_a)
    material = Material.objects.create(
        inspection=inspection,
        kind=Material.Kind.IMAGE,
        display_name="现场照片.jpg",
        mime_type="image/jpeg",
        extension=".jpg",
        sha256="2" * 64,
        byte_size=10,
        width=640,
        height=360,
        created_by=inspector_a,
    )
    image_path = Path(settings.MEDIA_ROOT) / (
        f"originals/inspections/{inspection.id}/{material.id}/original"
    )
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fixed-image")
    material.original_file.name = image_path.relative_to(settings.MEDIA_ROOT).as_posix()
    material.save(update_fields=("original_file",))

    class CapturingVisionProvider(FixedResponseProvider):
        def describe(self, material_id, frames):
            assert material_id == material.id
            assert frames == [(0, image_path)]
            return super().describe(material_id, frames)

    monkeypatch.setattr(handlers, "provider_for", lambda capability: CapturingVisionProvider())

    handlers.vision_handler(_job(material, ProcessingJob.TaskType.VISION, "image"))

    analysis = material.visual_analyses.get(input_revision=material.revision)
    assert analysis.descriptions == [
        {"timestamp_ms": 0, "text": "现场候选画面，具体业务含义需要人工确认。"}
    ]
    assert not material.derivatives.filter(kind=MaterialDerivative.Kind.KEYFRAME).exists()


@pytest.mark.django_db
def test_text_fact_extraction_reads_text_material_without_creating_a_fake_transcript(
    inspector_a, monkeypatch
) -> None:
    from fire_inspect.ai import handlers

    inspection = _inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="现场文字说明.txt",
        text="大厅存在堆放物品，相关区域被当作仓库使用。",
    )

    class CapturingFactProvider(FixedResponseProvider):
        def extract_facts(self, transcript, visual_descriptions):
            assert transcript.full_text == "大厅存在堆放物品，相关区域被当作仓库使用。"
            assert transcript.material_id == material.id
            assert visual_descriptions == ()
            return super().extract_facts(transcript, visual_descriptions)

    monkeypatch.setattr(handlers, "provider_for", lambda capability: CapturingFactProvider())

    handlers.fact_extraction_handler(_job(material, ProcessingJob.TaskType.FACT_EXTRACTION, "text"))

    facts = list(Fact.objects.filter(primary_source_material=material).order_by("content"))
    assert len(facts) == 2
    assert all(fact.needs_verification for fact in facts)
    assert all("文字说明" in fact.verification_note for fact in facts)
    assert all(not fact.media_ranges.exists() for fact in facts)
    assert not Transcript.objects.filter(material=material).exists()
