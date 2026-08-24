from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from django.conf import settings
from django.db import transaction

from fire_inspect.ai.factory import provider_for
from fire_inspect.ai.models import MaterialVisualAnalysis
from fire_inspect.ai.providers import ProviderFailure
from fire_inspect.ai.schemas import (
    FactExtractionOutput,
    SemanticRole,
    TranscriptOutput,
    TranscriptSegment,
    VisualDescription,
)
from fire_inspect.facts.services import publish_ai_fact_candidates
from fire_inspect.jobs.models import JobAttempt
from fire_inspect.jobs.registry import register
from fire_inspect.jobs.services import StaleJobInput, assert_active_lease
from fire_inspect.knowledge.django_models import (
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from fire_inspect.knowledge.embedding import (
    clause_embedding_text,
    embedding_input_fingerprint,
    validate_embedding_batch,
)
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.services import controlled_path
from fire_inspect.transcripts.models import Transcript
from fire_inspect.transcripts.services import publish_transcript_output


class StaleAIInput(StaleJobInput):
    pass


def _material_for(job) -> Material:
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    material = Material.objects.select_related("inspection").get(pk=job.scope_id)
    if material.inspection_id != job.inspection_id:
        raise StaleAIInput("任务材料不属于当前检查")
    if job.expected_scope_revision is not None and material.revision != job.expected_scope_revision:
        raise StaleAIInput("材料版本已变化，本次 AI 结果未发布")
    return material


def _provider_metadata(provider) -> tuple[str, str, str]:
    configuration = getattr(provider, "settings", None)
    provider_name = getattr(configuration, "provider", provider.__class__.__name__)
    model_name = getattr(configuration, "model", getattr(provider, "fixture_name", ""))
    fingerprint = sha256(
        f"{provider_name}|{getattr(configuration, 'endpoint', '')}|{model_name}".encode()
    ).hexdigest()
    return str(provider_name)[:100], str(model_name)[:100], fingerprint


def _record_provider(job, provider) -> None:
    provider_name, model_name, fingerprint = _provider_metadata(provider)
    JobAttempt.objects.filter(job=job, attempt_no=job.attempt_count).update(
        capability=job.task_type,
        provider_name=provider_name,
        model_name=model_name,
        config_fingerprint=fingerprint,
    )


def _derived_path(derivative: MaterialDerivative) -> Path:
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / derivative.storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ProviderFailure("AI 输入派生文件不可用，请先重试前置步骤")
    return path


@register("asr")
def asr_handler(job) -> None:
    material = _material_for(job)
    if material.kind not in {Material.Kind.VIDEO, Material.Kind.AUDIO}:
        raise ProviderFailure("只有视频或录音材料可以执行转写")
    if not material.duration_ms:
        raise ProviderFailure("材料时长未知，请先完成媒体探测")
    derivative = (
        material.derivatives.filter(kind=MaterialDerivative.Kind.AUDIO_EXTRACT)
        .order_by("-created_at")
        .first()
    )
    provider = provider_for("asr")
    _record_provider(job, provider)
    source = (
        controlled_path(material)
        if getattr(provider, "transcription_input", "audio") == "original"
        else (_derived_path(derivative) if derivative else controlled_path(material))
    )
    output = provider.transcribe(material.id, source, material.duration_ms)
    if output.material_id != material.id:
        raise ProviderFailure("AI 转写返回了错误的材料引用")
    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        locked = Material.objects.select_for_update().get(pk=material.id)
        if (
            job.expected_scope_revision is not None
            and locked.revision != job.expected_scope_revision
        ):
            raise StaleAIInput("材料版本已变化，本次 AI 结果未发布")
        publish_transcript_output(output, updated_by=job.created_by)


@register("vision")
def vision_handler(job) -> None:
    material = _material_for(job)
    if material.kind == Material.Kind.IMAGE:
        source = controlled_path(material)
        if not source.is_file():
            raise ProviderFailure("图片原件不可用，请检查材料后重试")
        frames = [(0, source)]
    elif material.kind == Material.Kind.VIDEO:
        derivatives = list(
            material.derivatives.filter(
                kind=MaterialDerivative.Kind.KEYFRAME,
                source_timestamp_ms__isnull=False,
            ).order_by("source_timestamp_ms", "id")
        )
        if not derivatives:
            raise ProviderFailure("没有可分析的关键画面，请先重试关键画面步骤")
        frames = [(item.source_timestamp_ms, _derived_path(item)) for item in derivatives]
    else:
        raise ProviderFailure("只有视频或图片材料可以执行视觉描述")
    provider = provider_for("vision")
    _record_provider(job, provider)
    descriptions = provider.describe(material.id, frames)
    if not descriptions:
        raise ProviderFailure("视觉服务未返回可用结果，未保存本次结果")
    if any(item.material_id != material.id for item in descriptions):
        raise ProviderFailure("视觉服务返回了错误的材料引用")
    provider_name, model_name, fingerprint = _provider_metadata(provider)
    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        locked = Material.objects.select_for_update().get(pk=material.id)
        if locked.revision != material.revision:
            raise StaleAIInput("材料版本已变化，本次 AI 结果未发布")
        MaterialVisualAnalysis.objects.update_or_create(
            material=locked,
            input_revision=locked.revision,
            defaults={
                "descriptions": [
                    {"timestamp_ms": item.timestamp_ms, "text": item.text} for item in descriptions
                ],
                "provider_name": provider_name,
                "model_fingerprint": f"{model_name}:{fingerprint}",
            },
        )


def _transcript_output(transcript: Transcript) -> TranscriptOutput:
    segments = tuple(
        TranscriptSegment(
            material_id=transcript.material_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            text=item.text,
            suggested_role=SemanticRole(item.confirmed_role or item.suggested_role),
        )
        for item in transcript.segments.order_by("sequence", "id")
    )
    return TranscriptOutput(full_text=transcript.full_text, segments=segments)


def _text_material_output(material: Material) -> TranscriptOutput:
    source = controlled_path(material)
    try:
        text = source.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ProviderFailure("文字材料内容不可用，请检查原件后重试") from exc
    if not text:
        raise ProviderFailure("文字材料内容为空，不能提取候选事实")
    return TranscriptOutput(
        full_text=text,
        segments=(
            TranscriptSegment(
                material_id=material.id,
                start_ms=0,
                end_ms=1,
                text=text,
                suggested_role=SemanticRole.UNKNOWN,
            ),
        ),
    )


def _without_text_timeline_evidence(output: FactExtractionOutput) -> FactExtractionOutput:
    notice = "候选来源为文字说明，需由检查人员人工核实；文字材料不产生时间范围证据。"
    return FactExtractionOutput(
        tuple(
            replace(
                fact,
                evidence=(),
                needs_verification=True,
                verification_note=" ".join(
                    item for item in (fact.verification_note.strip(), notice) if item
                ),
            )
            for fact in output.facts
        )
    )


@register("fact_extraction")
def fact_extraction_handler(job) -> None:
    material = _material_for(job)
    if material.kind == Material.Kind.TEXT:
        transcript_output = _text_material_output(material)
    elif material.kind in {Material.Kind.VIDEO, Material.Kind.AUDIO}:
        try:
            transcript = Transcript.objects.prefetch_related("segments").get(material=material)
        except Transcript.DoesNotExist as exc:
            raise ProviderFailure("缺少可用转写，请先完成或人工补充转写") from exc
        transcript_output = _transcript_output(transcript)
    else:
        raise ProviderFailure("此材料类型不支持直接提取候选事实")
    analysis = MaterialVisualAnalysis.objects.filter(
        material=material, input_revision=material.revision
    ).first()
    visual_descriptions = tuple(
        VisualDescription(
            material_id=material.id,
            timestamp_ms=int(item["timestamp_ms"]),
            text=str(item["text"]),
        )
        for item in (analysis.descriptions if analysis else [])
    )
    provider = provider_for("llm")
    _record_provider(job, provider)
    output = provider.extract_facts(transcript_output, visual_descriptions)
    if not output.facts:
        raise ProviderFailure("事实提取未返回可用候选，未保存本次结果")
    if material.kind == Material.Kind.TEXT:
        output = _without_text_timeline_evidence(output)
    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        locked = (
            Material.objects.select_for_update().select_related("inspection").get(pk=material.id)
        )
        if (
            job.expected_scope_revision is not None
            and locked.revision != job.expected_scope_revision
        ):
            raise StaleAIInput("材料版本已变化，本次 AI 结果未发布")
        publish_ai_fact_candidates(
            inspection=locked.inspection,
            primary_material=locked,
            input_revision=locked.revision,
            output=output,
            created_by=job.created_by,
        )


def _embedding_document_for(job):
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    if job.scope_type != "knowledge_document" or job.inspection_id is not None:
        raise StaleAIInput("Embedding 任务范围无效，本次结果未发布")
    try:
        document = KnowledgeDocumentRecord.objects.get(pk=job.scope_id)
    except KnowledgeDocumentRecord.DoesNotExist as exc:
        raise StaleAIInput("知识文档已不存在，本次结果未发布") from exc
    if document.processing_status != KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED:
        raise StaleAIInput("知识文档解析结果不可用，本次结果未发布")
    if job.expected_scope_revision is not None and document.revision != job.expected_scope_revision:
        raise StaleAIInput("知识文档版本已变化，本次 Embedding 结果未发布")
    clauses = list(document.clauses.order_by("sequence", "id"))
    if not clauses:
        raise ProviderFailure("知识文档没有可索引条款，请先重试解析步骤")
    if embedding_input_fingerprint(document, clauses) != job.input_fingerprint:
        raise StaleAIInput("Embedding 输入或配置已变化，本次结果未发布")
    return document, clauses


@register("embedding")
def embedding_handler(job) -> None:
    document, clauses = _embedding_document_for(job)
    provider = provider_for("embedding")
    _record_provider(job, provider)
    texts = tuple(clause_embedding_text(document, clause) for clause in clauses)
    outputs = validate_embedding_batch(provider.embed(texts), expected_count=len(texts))
    model_fingerprint = str(outputs[0].model_fingerprint).strip()
    dimensions = len(outputs[0].values)

    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        try:
            locked = KnowledgeDocumentRecord.objects.select_for_update().get(pk=document.id)
        except KnowledgeDocumentRecord.DoesNotExist as exc:
            raise StaleAIInput("知识文档已不存在，本次结果未发布") from exc
        if locked.processing_status != KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED:
            raise StaleAIInput("知识文档解析结果不可用，本次结果未发布")
        if (
            job.expected_scope_revision is not None
            and locked.revision != job.expected_scope_revision
        ):
            raise StaleAIInput("知识文档版本已变化，本次 Embedding 结果未发布")
        current_clauses = list(locked.clauses.order_by("sequence", "id"))
        if embedding_input_fingerprint(locked, current_clauses) != job.input_fingerprint:
            raise StaleAIInput("Embedding 输入或配置已变化，本次结果未发布")
        if [item.id for item in current_clauses] != [item.id for item in clauses]:
            raise StaleAIInput("知识条款集合已变化，本次 Embedding 结果未发布")

        for clause, output in zip(current_clauses, outputs, strict=True):
            KnowledgeEmbeddingRecord.objects.update_or_create(
                clause=clause,
                model_fingerprint=model_fingerprint,
                defaults={
                    "input_revision": locked.revision,
                    "dimensions": dimensions,
                    "values": [float(value) for value in output.values],
                    "status": KnowledgeEmbeddingRecord.Status.SUCCEEDED,
                    "safe_error_message": "",
                },
            )
