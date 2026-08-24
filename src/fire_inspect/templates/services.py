from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from fire_inspect.audit.models import AuditEvent
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import (
    IdempotencyConflict,
    InvalidJobTransition,
    enqueue_job,
    retry_job,
)
from fire_inspect.templates.domain import TemplateDocumentType
from fire_inspect.templates.security import (
    DocxSecurityError,
    read_docx_source,
    validate_docx,
)

from .models import (
    TemplateMappingRevision,
    TemplateRegion,
    TemplateVersion,
    WordTemplate,
)


class TemplateServiceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


TEMPLATE_RECOGNIZER_VERSION = "1.0"


def _payload(upload: BinaryIO | bytes | bytearray | memoryview) -> bytes:
    if isinstance(upload, (bytes, bytearray, memoryview)):
        return bytes(upload)
    try:
        upload.seek(0)
        value = upload.read()
    except (AttributeError, OSError) as exc:
        raise TemplateServiceError("template_unreadable", "模板文件无法读取。") from exc
    if not isinstance(value, bytes):
        raise TemplateServiceError("template_unreadable", "模板文件无法读取。")
    return value


def _validate_declared_type(declared_type: str, recognized_type: TemplateDocumentType) -> None:
    if declared_type not in WordTemplate.Type.values:
        raise TemplateServiceError("invalid_document_type", "文书类型不正确。")
    if (
        recognized_type is not TemplateDocumentType.UNKNOWN
        and recognized_type.value != declared_type
    ):
        raise TemplateServiceError("template_type_mismatch", "上传模板内容与所选文书类型不一致。")


def template_recognition_input_fingerprint(
    *,
    template_name: str,
    document_type: str,
    version_no: int,
    source_sha256: str,
) -> str:
    return sha256(
        (
            "template-recognition|"
            f"{template_name.strip()}|{document_type}|{version_no}|"
            f"{source_sha256}|{TEMPLATE_RECOGNIZER_VERSION}"
        ).encode()
    ).hexdigest()


def enqueue_template_recognition(
    *,
    template: WordTemplate,
    version: TemplateVersion,
    actor,
    idempotency_key: str | None = None,
):
    fingerprint = template_recognition_input_fingerprint(
        template_name=template.name,
        document_type=template.document_type,
        version_no=version.version_no,
        source_sha256=version.sha256,
    )
    key = idempotency_key or (
        f"system:template-recognition:{template.id}:{version.version_no}:{fingerprint}"
    )
    return enqueue_job(
        task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION,
        scope_type="word_template",
        scope_id=template.id,
        inspection=None,
        input_fingerprint=fingerprint,
        idempotency_key=key,
        created_by=actor,
        expected_scope_revision=version.version_no,
    )


def _create_mapping_from_recognition(
    *,
    version: TemplateVersion,
    recognition,
    actor,
    revision_no: int,
) -> TemplateMappingRevision:
    mapping = TemplateMappingRevision.objects.create(
        template_version=version,
        revision_no=revision_no,
        state=TemplateMappingRevision.State.DRAFT,
        recognizer_version=recognition.recognizer_version,
        warnings=list(recognition.warnings),
        created_by=actor,
    )
    TemplateRegion.objects.bulk_create(
        [
            TemplateRegion(
                mapping_revision=mapping,
                business_field_key=region.business_field_key,
                display_name=region.display_name,
                repeat_scope=region.repeat_scope.value,
                fill_strategy=region.fill_strategy.value,
                locator=asdict(region.locator),
                style_policy=dict(region.style_policy),
                confidence=region.confidence,
                enabled=region.enabled,
                required=region.required,
                sequence=region.sequence,
            )
            for region in recognition.regions
        ]
    )
    return mapping


@transaction.atomic
def create_word_template(
    *,
    name: str,
    document_type: str,
    upload,
    actor,
    original_filename: str | None = None,
    idempotency_key: str | None = None,
) -> WordTemplate:
    normalized_name = name.strip()
    if not normalized_name:
        raise TemplateServiceError("template_name_required", "模板名称不能为空。")
    payload = _payload(upload)
    filename = original_filename or getattr(upload, "name", "template.docx")
    try:
        report = validate_docx(payload, filename=filename)
    except DocxSecurityError as exc:
        raise TemplateServiceError("unsafe_template", str(exc)) from exc
    if document_type not in WordTemplate.Type.values:
        raise TemplateServiceError("invalid_document_type", "文书类型不正确。")

    fingerprint = template_recognition_input_fingerprint(
        template_name=normalized_name,
        document_type=document_type,
        version_no=1,
        source_sha256=report.sha256,
    )
    if idempotency_key:
        previous = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
        if previous is not None:
            if (
                previous.task_type != ProcessingJob.TaskType.TEMPLATE_RECOGNITION
                or previous.scope_type != "word_template"
                or previous.input_fingerprint != fingerprint
                or previous.scope_id is None
            ):
                raise IdempotencyConflict("idempotency key was already used for another command")
            try:
                existing = WordTemplate.objects.get(pk=previous.scope_id)
            except WordTemplate.DoesNotExist as exc:
                raise IdempotencyConflict(
                    "idempotency key points to an unavailable command target"
                ) from exc
            existing._recognition_job = previous
            existing._recognition_job_created = False
            return existing

    template = WordTemplate.objects.create(
        name=normalized_name,
        document_type=document_type,
        recognition_status=WordTemplate.RecognitionStatus.QUEUED,
        created_by=actor,
    )
    version = TemplateVersion(
        template=template,
        version_no=1,
        original_filename=report.normalized_filename,
        sha256=report.sha256,
        validation_result=asdict(report),
        created_by=actor,
    )
    version.original_file.save("original.docx", ContentFile(payload), save=False)
    try:
        version.save(force_insert=True)
        job, job_created = enqueue_template_recognition(
            template=template,
            version=version,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        AuditEvent.append(
            actor=actor,
            object_type="word_template",
            object_id=template.id,
            action="created",
            masked_change_summary=f"template created: type={document_type}",
        )
    except Exception:
        version.original_file.storage.delete(version.original_file.name)
        raise
    template._recognition_job = job
    template._recognition_job_created = job_created
    return template


@transaction.atomic
def create_template_version(
    *,
    template: WordTemplate,
    upload,
    actor,
    idempotency_key: str | None = None,
) -> TemplateVersion:
    template = WordTemplate.objects.select_for_update().get(pk=template.pk)
    payload = _payload(upload)
    filename = getattr(upload, "name", "template.docx")
    try:
        report = validate_docx(payload, filename=filename)
    except DocxSecurityError as exc:
        raise TemplateServiceError("unsafe_template", str(exc)) from exc

    if idempotency_key:
        previous = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
        if previous is not None:
            if (
                previous.task_type != ProcessingJob.TaskType.TEMPLATE_RECOGNITION
                or previous.scope_type != "word_template"
                or previous.scope_id != template.id
            ):
                raise IdempotencyConflict("idempotency key was already used for another command")
            matched = next(
                (
                    item
                    for item in template.versions.all()
                    if item.sha256 == report.sha256
                    and template_recognition_input_fingerprint(
                        template_name=template.name,
                        document_type=template.document_type,
                        version_no=item.version_no,
                        source_sha256=item.sha256,
                    )
                    == previous.input_fingerprint
                ),
                None,
            )
            if matched is None:
                raise IdempotencyConflict("idempotency key was already used for another command")
            matched._recognition_job = previous
            matched._recognition_job_created = False
            return matched

    latest = template.versions.order_by("-version_no").first()
    if latest is not None and latest.sha256 == report.sha256:
        job, job_created = enqueue_template_recognition(
            template=template,
            version=latest,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        latest._recognition_job = job
        latest._recognition_job_created = job_created
        return latest
    version_no = (
        template.versions.order_by("-version_no").values_list("version_no", flat=True).first() or 0
    ) + 1
    version = TemplateVersion(
        template=template,
        version_no=version_no,
        original_filename=report.normalized_filename,
        sha256=report.sha256,
        validation_result=asdict(report),
        created_by=actor,
    )
    version.original_file.save("original.docx", ContentFile(payload), save=False)
    try:
        version.save(force_insert=True)
        job, job_created = enqueue_template_recognition(
            template=template,
            version=version,
            actor=actor,
            idempotency_key=idempotency_key,
        )
    except Exception:
        version.original_file.storage.delete(version.original_file.name)
        raise
    template.recognition_status = WordTemplate.RecognitionStatus.QUEUED
    template.safe_error_message = ""
    template.save(update_fields=("recognition_status", "safe_error_message"))
    version._recognition_job = job
    version._recognition_job_created = job_created
    return version


def template_payload(version: TemplateVersion) -> bytes:
    try:
        with version.original_file.open("rb") as stream:
            payload = stream.read()
    except OSError as exc:
        raise TemplateServiceError("template_file_unavailable", "模板原文件暂时不可用。") from exc
    try:
        report = validate_docx(payload, filename=version.original_filename)
    except DocxSecurityError as exc:
        raise TemplateServiceError("template_integrity_failed", str(exc)) from exc
    if report.sha256 != version.sha256:
        raise TemplateServiceError("template_integrity_failed", "模板原文件完整性校验失败。")
    return payload


@transaction.atomic
def publish_template_recognition(
    *,
    template: WordTemplate,
    version: TemplateVersion,
    recognition,
    actor,
) -> TemplateMappingRevision:
    template = WordTemplate.objects.select_for_update().get(pk=template.pk)
    latest = template.versions.order_by("-version_no").first()
    if latest is None or latest.id != version.id:
        raise TemplateServiceError("template_input_changed", "模板版本已变化，本次识别结果未发布。")
    _validate_declared_type(template.document_type, recognition.document_type)
    draft = (
        latest.mapping_revisions.filter(state=TemplateMappingRevision.State.DRAFT)
        .order_by("-revision_no")
        .first()
    )
    if draft is not None:
        draft.regions.all().delete()
        draft.warnings = list(recognition.warnings)
        draft.recognizer_version = recognition.recognizer_version
        draft.save(update_fields=("warnings", "recognizer_version"))
        mapping = draft
        TemplateRegion.objects.bulk_create(
            [
                TemplateRegion(
                    mapping_revision=mapping,
                    business_field_key=region.business_field_key,
                    display_name=region.display_name,
                    repeat_scope=region.repeat_scope.value,
                    fill_strategy=region.fill_strategy.value,
                    locator=asdict(region.locator),
                    style_policy=dict(region.style_policy),
                    confidence=region.confidence,
                    enabled=region.enabled,
                    required=region.required,
                    sequence=region.sequence,
                )
                for region in recognition.regions
            ]
        )
    else:
        next_revision = (
            latest.mapping_revisions.order_by("-revision_no")
            .values_list("revision_no", flat=True)
            .first()
            or 0
        ) + 1
        mapping = _create_mapping_from_recognition(
            version=latest,
            recognition=recognition,
            actor=actor,
            revision_no=next_revision,
        )
    template.recognition_status = WordTemplate.RecognitionStatus.SUCCEEDED
    template.safe_error_message = ""
    template.save(update_fields=("recognition_status", "safe_error_message"))
    AuditEvent.append(
        actor=actor,
        object_type="word_template",
        object_id=template.id,
        action="recognized",
        masked_change_summary=(
            f"template recognized: version={latest.version_no}, regions={len(recognition.regions)}"
        ),
    )
    return mapping


@transaction.atomic
def retry_template_recognition(
    *,
    template: WordTemplate,
    actor,
    idempotency_key: str,
) -> WordTemplate:
    template_id = template.pk
    job = (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION,
            scope_type="word_template",
            scope_id=template_id,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if job is None:
        raise InvalidJobTransition("template recognition job does not exist")
    repeated = job.last_retry_idempotency_key == idempotency_key
    job = retry_job(
        job_id=job.id,
        actor=actor,
        idempotency_key=idempotency_key,
    )
    template = WordTemplate.objects.select_for_update().get(pk=template_id)
    if job.status == ProcessingJob.Status.FAILED:
        template.recognition_status = WordTemplate.RecognitionStatus.FAILED
        template.safe_error_message = job.safe_error_message
    else:
        template.recognition_status = WordTemplate.RecognitionStatus.QUEUED
        template.safe_error_message = ""
    template.save(update_fields=("recognition_status", "safe_error_message"))
    template._recognition_job = job
    template._recognition_retry_repeated = repeated
    return template


@transaction.atomic
def update_template_region(*, region: TemplateRegion, values: dict) -> TemplateRegion:
    region = (
        TemplateRegion.objects.select_for_update()
        .select_related("mapping_revision")
        .get(pk=region.pk)
    )
    if region.mapping_revision.state != TemplateMappingRevision.State.DRAFT:
        raise TemplateServiceError(
            "confirmed_mapping_immutable",
            "已确认映射不可原位修改，请先创建新草稿。",
        )
    allowed = {"display_name", "fill_strategy", "repeat_scope", "enabled", "required"}
    unknown = set(values) - allowed
    if unknown:
        raise TemplateServiceError("invalid_region_field", "模板区域修改字段不受支持。")
    for key, value in values.items():
        setattr(region, key, value)
    try:
        region.save()
    except ValidationError as exc:
        raise TemplateServiceError("invalid_template_region", "; ".join(exc.messages)) from exc
    return region


@transaction.atomic
def create_template_region(*, mapping: TemplateMappingRevision, values: dict) -> TemplateRegion:
    mapping = TemplateMappingRevision.objects.select_for_update().get(pk=mapping.pk)
    if mapping.state != TemplateMappingRevision.State.DRAFT:
        raise TemplateServiceError(
            "confirmed_mapping_immutable",
            "已确认映射不可原位修改，请先创建新草稿。",
        )
    display_name = str(values.get("display_name", "")).strip()
    if not display_name:
        raise TemplateServiceError("region_name_required", "业务区域名称不能为空。")
    business_field_key = str(values.get("business_field_key", "")).strip()
    if not business_field_key:
        business_field_key = f"manual_{mapping.regions.count() + 1}"
    if not business_field_key.replace("_", "").isalnum() or not business_field_key.isascii():
        raise TemplateServiceError("invalid_business_field_key", "业务字段标识格式不正确。")
    if mapping.regions.filter(business_field_key=business_field_key).exists():
        raise TemplateServiceError("duplicate_business_field", "该业务字段已存在映射。")
    repeat_scope = str(values.get("repeat_scope", TemplateRegion.RepeatScope.SINGLE))
    fill_strategy = str(values.get("fill_strategy", TemplateRegion.FillStrategy.LABEL_SUFFIX))
    if repeat_scope not in TemplateRegion.RepeatScope.values:
        raise TemplateServiceError("invalid_repeat_scope", "重复范围不正确。")
    if fill_strategy not in TemplateRegion.FillStrategy.values:
        raise TemplateServiceError("invalid_fill_strategy", "填充方式不正确。")
    anchor = str(values.get("anchor", values.get("location_preview", ""))).strip()
    locator = {
        "part": "word/document.xml",
        "anchor": anchor or "管理员人工定位区域",
        "manual_mapping": True,
    }
    sequence = (
        mapping.regions.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    ) + 1
    region = TemplateRegion(
        mapping_revision=mapping,
        business_field_key=business_field_key,
        display_name=display_name,
        repeat_scope=repeat_scope,
        fill_strategy=fill_strategy,
        locator=locator,
        style_policy={},
        confidence=0.0,
        enabled=bool(values.get("enabled", True)),
        required=bool(values.get("required", False)),
        sequence=sequence,
    )
    try:
        region.save()
    except ValidationError as exc:
        raise TemplateServiceError("invalid_template_region", "; ".join(exc.messages)) from exc
    return region


@transaction.atomic
def revise_template_mapping(*, template: WordTemplate, actor) -> TemplateMappingRevision:
    template = WordTemplate.objects.select_for_update().get(pk=template.pk)
    version = template.versions.order_by("-version_no").first()
    if version is None:
        raise TemplateServiceError("template_version_missing", "模板没有可编辑的版本。")
    existing = (
        version.mapping_revisions.filter(state=TemplateMappingRevision.State.DRAFT)
        .order_by("-revision_no")
        .first()
    )
    if existing is not None:
        return existing
    source = template.active_mapping_revision
    if source is None or source.template_version_id != version.id:
        source = (
            version.mapping_revisions.filter(state=TemplateMappingRevision.State.CONFIRMED)
            .order_by("-revision_no")
            .first()
        )
    if source is None:
        raise TemplateServiceError(
            "confirmed_mapping_required", "只有已确认映射可以创建下一版草稿。"
        )
    revision_no = (
        version.mapping_revisions.order_by("-revision_no")
        .values_list("revision_no", flat=True)
        .first()
        or 0
    ) + 1
    draft = TemplateMappingRevision.objects.create(
        template_version=version,
        revision_no=revision_no,
        state=TemplateMappingRevision.State.DRAFT,
        recognizer_version=source.recognizer_version,
        warnings=source.warnings,
        created_by=actor,
    )
    TemplateRegion.objects.bulk_create(
        [
            TemplateRegion(
                mapping_revision=draft,
                business_field_key=region.business_field_key,
                display_name=region.display_name,
                repeat_scope=region.repeat_scope,
                fill_strategy=region.fill_strategy,
                locator=region.locator,
                style_policy=region.style_policy,
                confidence=region.confidence,
                enabled=region.enabled,
                required=region.required,
                sequence=region.sequence,
            )
            for region in source.regions.all()
        ]
    )
    return draft


@transaction.atomic
def confirm_template_mapping(*, template: WordTemplate, actor) -> TemplateMappingRevision:
    template = WordTemplate.objects.select_for_update().get(pk=template.pk)
    mapping = template.current_mapping_revision
    if mapping is None:
        raise TemplateServiceError("mapping_missing", "模板尚无映射。")
    mapping = TemplateMappingRevision.objects.select_for_update().get(pk=mapping.pk)
    if mapping.state == TemplateMappingRevision.State.CONFIRMED:
        return mapping
    if mapping.state != TemplateMappingRevision.State.DRAFT:
        raise TemplateServiceError("mapping_not_draft", "只有草稿映射可以确认。")
    regions = list(mapping.regions.order_by("sequence", "id"))
    missing = [
        region.display_name
        for region in regions
        if region.required
        and (not region.enabled or not str(region.locator.get("anchor", "")).strip())
    ]
    if missing:
        raise TemplateServiceError(
            "mapping_required_region_missing",
            f"必填模板区域未启用或未定位：{'、'.join(missing)}。",
        )
    invalid_signatures = [
        region.display_name
        for region in regions
        if "signature" in region.business_field_key
        and region.fill_strategy != TemplateRegion.FillStrategy.PRESERVE_BLANK
    ]
    if invalid_signatures:
        raise TemplateServiceError("signature_region_must_be_blank", "签名区域必须保持空白。")

    previous = template.active_mapping_revision
    if previous is not None and previous.pk != mapping.pk:
        previous.state = TemplateMappingRevision.State.SUPERSEDED
        previous.save(update_fields=("state",))
    mapping.state = TemplateMappingRevision.State.CONFIRMED
    mapping.confirmed_by = actor
    mapping.confirmed_at = timezone.now()
    mapping.save(update_fields=("state", "confirmed_by", "confirmed_at"))
    template.active_mapping_revision = mapping
    template.active = True
    template.save(update_fields=("active_mapping_revision", "active"))

    if previous is not None and previous.pk != mapping.pk:
        from fire_inspect.documents.services import invalidate_for_template_mapping

        invalidate_for_template_mapping(
            template=template,
            new_mapping=mapping,
            actor=actor,
        )
    AuditEvent.append(
        actor=actor,
        object_type="template_mapping",
        object_id=mapping.id,
        action="confirmed",
        masked_change_summary=f"mapping confirmed: revision={mapping.revision_no}",
    )
    return mapping


def active_template_for_type(document_type: str) -> WordTemplate | None:
    return (
        WordTemplate.objects.filter(
            document_type=document_type,
            active=True,
            active_mapping_revision__state=TemplateMappingRevision.State.CONFIRMED,
        )
        .select_related(
            "active_mapping_revision",
            "active_mapping_revision__template_version",
        )
        .order_by("-created_at")
        .first()
    )


def initialize_authoritative_templates(*, actor=None) -> dict[str, WordTemplate]:
    sources = {
        "record": ("监督检查记录表", "监督检查记录表.docx"),
        "photo": ("照片文书", "照片.docx"),
        "interview": ("询问笔录", "询问笔录.docx"),
    }
    root = Path(settings.BASE_DIR) / "docs" / "templates"
    initialized: dict[str, WordTemplate] = {}
    for document_type, (name, filename) in sources.items():
        path = root / filename
        payload = read_docx_source(path)
        report = validate_docx(payload, filename=filename)
        existing_version = (
            TemplateVersion.objects.filter(
                template__document_type=document_type,
                sha256=report.sha256,
            )
            .select_related("template")
            .order_by("created_at")
            .first()
        )
        if existing_version is None:
            template = create_word_template(
                name=name,
                document_type=document_type,
                upload=payload,
                actor=actor,
                original_filename=filename,
            )
        else:
            template = existing_version.template
        if template.active_mapping_revision_id is None:
            version = template.versions.order_by("-version_no").first()
            job = (
                ProcessingJob.objects.filter(
                    task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION,
                    scope_type="word_template",
                    scope_id=template.id,
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if job is None:
                job, _ = enqueue_template_recognition(
                    template=template,
                    version=version,
                    actor=actor,
                )
            elif job.status == ProcessingJob.Status.FAILED:
                template = retry_template_recognition(
                    template=template,
                    actor=actor,
                    idempotency_key=f"system:template-bootstrap-retry:{job.id}",
                )
                job = template._recognition_job
            if job.status == ProcessingJob.Status.QUEUED:
                # Bootstrap still uses the persistent lease/fencing Worker path;
                # it only drives this exact seed job so initialization is usable
                # before the long-running worker service starts.
                from fire_inspect.jobs.management.commands.runworker import (
                    Command as WorkerCommand,
                )
                from fire_inspect.jobs.services import acquire_job_by_id

                claimed = acquire_job_by_id(
                    job_id=job.id,
                    worker_id="template-bootstrap",
                )
                if claimed is not None:
                    WorkerCommand._execute(claimed.id, claimed.lease_token)
            job.refresh_from_db()
            if job.status != ProcessingJob.Status.SUCCEEDED:
                raise TemplateServiceError(
                    "template_recognition_incomplete",
                    job.safe_error_message or "权威模板识别尚未成功，请运行 Worker 后重试初始化。",
                )
            template.refresh_from_db()
            confirm_template_mapping(template=template, actor=actor)
            template.refresh_from_db()
        initialized[document_type] = template
    return initialized
