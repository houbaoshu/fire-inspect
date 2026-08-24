from __future__ import annotations

from django.db import transaction

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.registry import register
from fire_inspect.jobs.services import (
    LostLease,
    RecoverableJobFailure,
    StaleJobInput,
    assert_active_lease,
)
from fire_inspect.templates.models import TemplateMappingRevision, WordTemplate
from fire_inspect.templates.recognizer import recognize_template
from fire_inspect.templates.security import DocxSecurityError
from fire_inspect.templates.services import (
    TEMPLATE_RECOGNIZER_VERSION,
    TemplateServiceError,
    publish_template_recognition,
    template_payload,
    template_recognition_input_fingerprint,
)


def _template_and_version_for(job):
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    if job.scope_type != "word_template" or job.inspection_id is not None:
        raise StaleJobInput("模板识别任务范围无效，本次结果未发布。")
    try:
        template = WordTemplate.objects.get(pk=job.scope_id)
    except WordTemplate.DoesNotExist as exc:
        raise StaleJobInput("模板已不存在，本次结果未发布。") from exc
    version = template.versions.order_by("-version_no").first()
    if version is None:
        raise StaleJobInput("模板版本已不存在，本次结果未发布。")
    fingerprint = template_recognition_input_fingerprint(
        template_name=template.name,
        document_type=template.document_type,
        version_no=version.version_no,
        source_sha256=version.sha256,
    )
    if fingerprint != job.input_fingerprint or (
        job.expected_scope_revision is not None
        and job.expected_scope_revision != version.version_no
    ):
        raise StaleJobInput("模板版本或识别规则已变化，本次结果未发布。")
    return template, version


def _already_published(template: WordTemplate, version) -> bool:
    if template.recognition_status != WordTemplate.RecognitionStatus.SUCCEEDED:
        return False
    return version.mapping_revisions.filter(
        state__in=(
            TemplateMappingRevision.State.DRAFT,
            TemplateMappingRevision.State.CONFIRMED,
        ),
        recognizer_version=TEMPLATE_RECOGNIZER_VERSION,
    ).exists()


def _mark_failed(job, message: str) -> None:
    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        template, _ = _template_and_version_for(job)
        locked = WordTemplate.objects.select_for_update().get(pk=template.id)
        locked.recognition_status = WordTemplate.RecognitionStatus.FAILED
        locked.safe_error_message = message[:500]
        locked.save(update_fields=("recognition_status", "safe_error_message"))


@register(ProcessingJob.TaskType.TEMPLATE_RECOGNITION)
def template_recognition_handler(job) -> None:
    template, version = _template_and_version_for(job)
    if _already_published(template, version):
        return
    WordTemplate.objects.filter(pk=template.pk).update(
        recognition_status=WordTemplate.RecognitionStatus.RUNNING,
        safe_error_message="",
    )

    try:
        payload = template_payload(version)
        recognition = recognize_template(payload, filename=version.original_filename)
        with transaction.atomic():
            assert_active_lease(job_id=job.id, lease_token=job.lease_token)
            current_template, current_version = _template_and_version_for(job)
            publish_template_recognition(
                template=current_template,
                version=current_version,
                recognition=recognition,
                actor=job.created_by,
            )
    except (LostLease, StaleJobInput):
        raise
    except TemplateServiceError as exc:
        if exc.code == "template_input_changed":
            raise StaleJobInput(exc.message) from exc
        _mark_failed(job, exc.message)
        raise RecoverableJobFailure(code=exc.code, message=exc.message) from exc
    except DocxSecurityError as exc:
        message = str(exc) or "模板原件校验失败，请检查原件后重试。"
        _mark_failed(job, message)
        raise RecoverableJobFailure(
            code="template_integrity_failed",
            message=message,
        ) from exc
    except Exception:
        _mark_failed(job, "模板识别失败，可稍后仅重试识别步骤。")
        raise
