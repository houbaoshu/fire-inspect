from __future__ import annotations

from django.db import transaction

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.registry import register
from fire_inspect.jobs.services import (
    IdempotencyConflict,
    StaleJobInput,
    enqueue_job,
)

from .models import InspectionDocument
from .services import (
    DocumentRevisionConflict,
    DocumentServiceError,
    document_generation_input_fingerprint,
    generate_document,
    record_generation_requested,
)


def _input_fingerprint(document: InspectionDocument) -> str:
    return document_generation_input_fingerprint(document)


def _idempotency_conflict() -> DocumentServiceError:
    return DocumentServiceError(
        "idempotency_conflict",
        "此幂等键已用于其他操作。",
        status_code=409,
    )


@transaction.atomic
def enqueue_document_generation(
    *,
    document: InspectionDocument,
    actor,
    expected_revision: int,
    idempotency_key: str,
) -> tuple[ProcessingJob | None, bool]:
    """Bind one immutable document input revision to one persistent job."""

    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key:
        raise DocumentServiceError("idempotency_key_required", "请提交幂等键。")

    existing = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        if (
            existing.task_type != ProcessingJob.TaskType.DOCUMENT_GENERATION
            or existing.scope_type != "document"
            or existing.scope_id != document.id
            or existing.inspection_id != document.inspection_id
            or existing.expected_scope_revision != expected_revision
        ):
            raise _idempotency_conflict()
        return existing, False

    locked = (
        InspectionDocument.objects.select_for_update()
        .select_related("inspection", "current_mapping_revision")
        .get(pk=document.pk)
    )
    if locked.revision != expected_revision:
        raise DocumentRevisionConflict(locked.revision)
    if locked.state == InspectionDocument.State.CONFIRMED:
        raise DocumentServiceError(
            "confirmed_document_generation_forbidden",
            "已确认文书不能在后台重新生成，请先通过显式编辑退回初稿。",
            status_code=409,
        )
    if (
        locked.generation_valid
        and locked.generator_status == InspectionDocument.GeneratorStatus.SUCCEEDED
    ):
        return None, False

    try:
        job, created = enqueue_job(
            task_type=ProcessingJob.TaskType.DOCUMENT_GENERATION,
            scope_type="document",
            scope_id=locked.id,
            inspection=locked.inspection,
            input_fingerprint=_input_fingerprint(locked),
            idempotency_key=idempotency_key,
            created_by=actor,
            expected_scope_revision=locked.revision,
        )
    except IdempotencyConflict as exc:
        raise _idempotency_conflict() from exc

    previous_generator_status = locked.generator_status
    previous_generator_job_id = locked.generator_job_id
    previous_generation_valid = locked.generation_valid
    locked.generator_job_id = job.id
    if job.status == ProcessingJob.Status.FAILED:
        locked.generator_status = InspectionDocument.GeneratorStatus.FAILED
        locked.generator_safe_error = job.safe_error_message
    elif job.status == ProcessingJob.Status.SUPERSEDED:
        locked.generator_status = InspectionDocument.GeneratorStatus.SUPERSEDED
        locked.generator_safe_error = job.safe_error_message
    elif job.status == ProcessingJob.Status.SUCCEEDED:
        locked.generator_status = InspectionDocument.GeneratorStatus.SUCCEEDED
        locked.generator_safe_error = ""
    else:
        locked.generator_status = (
            InspectionDocument.GeneratorStatus.RUNNING
            if job.status == ProcessingJob.Status.RUNNING
            else InspectionDocument.GeneratorStatus.QUEUED
        )
        locked.generator_safe_error = ""
    locked.save(
        update_fields=(
            "generator_job_id",
            "generator_status",
            "generator_safe_error",
            "updated_at",
        )
    )
    if created:
        record_generation_requested(
            document=locked,
            actor=actor,
            job=job,
            previous_generator_status=previous_generator_status,
            previous_generator_job_id=previous_generator_job_id,
            previous_generation_valid=previous_generation_valid,
        )
    return job, created


@register(ProcessingJob.TaskType.DOCUMENT_GENERATION)
def document_generation_handler(job: ProcessingJob) -> None:
    try:
        document = InspectionDocument.objects.get(pk=job.scope_id)
    except InspectionDocument.DoesNotExist as exc:
        raise StaleJobInput("待生成文书已不存在") from exc
    if job.scope_type != "document" or document.inspection_id != job.inspection_id:
        raise StaleJobInput("文书生成任务范围无效")
    if job.expected_scope_revision is None:
        raise StaleJobInput("文书生成任务缺少输入修订")
    try:
        generate_document(
            document=document,
            actor=job.created_by,
            expected_revision=job.expected_scope_revision,
            generation_job=job,
        )
    except DocumentRevisionConflict as exc:
        raise StaleJobInput("文书输入修订已变化，本次结果未发布") from exc
    except DocumentServiceError as exc:
        if exc.code == "generation_superseded":
            raise StaleJobInput(exc.message) from exc
        raise
