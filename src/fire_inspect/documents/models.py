from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class InspectionDocument(models.Model):
    class Type(models.TextChoices):
        RECORD = "record", "消防监督检查记录"
        PHOTO = "photo", "照片文书"
        INTERVIEW = "interview", "询问笔录"

    class State(models.TextChoices):
        DRAFT = "draft", "初稿"
        CONFIRMED = "confirmed", "已确认"

    class GeneratorStatus(models.TextChoices):
        IDLE = "idle", "未生成"
        QUEUED = "queued", "待生成"
        RUNNING = "running", "生成中"
        SUCCEEDED = "succeeded", "已生成"
        FAILED = "failed", "生成失败"
        SUPERSEDED = "superseded", "结果已过期"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="documents"
    )
    document_type = models.CharField(max_length=16, choices=Type.choices)
    name = models.CharField(max_length=300)
    template_version = models.ForeignKey(
        "templates.TemplateVersion",
        on_delete=models.PROTECT,
        related_name="documents",
    )
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    revision = models.PositiveBigIntegerField(default=1)
    current_mapping_revision = models.ForeignKey(
        "templates.TemplateMappingRevision",
        on_delete=models.PROTECT,
        related_name="documents",
    )
    last_generated_revision = models.ForeignKey(
        "DocumentRevision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="current_for_documents",
    )
    generator_version = models.CharField(max_length=64, default="1.0")
    generator_status = models.CharField(
        max_length=16, choices=GeneratorStatus.choices, default=GeneratorStatus.IDLE
    )
    generator_job_id = models.UUIDField(null=True, blank=True)
    generator_safe_error = models.CharField(max_length=300, blank=True)
    generation_valid = models.BooleanField(default=False)
    upgrade_available = models.BooleanField(default=False)
    invalidation_reason = models.CharField(max_length=500, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="confirmed_documents",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("document_type", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("inspection", "document_type"),
                name="unique_document_type_per_inspection",
            )
        ]
        indexes = [models.Index(fields=("inspection", "document_type"))]


class DocumentFieldValue(models.Model):
    class Origin(models.TextChoices):
        AI = "ai", "AI"
        MANUAL = "manual", "人工"
        SYSTEM = "system", "系统"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="field_values"
    )
    business_field_key = models.CharField(max_length=100)
    value_json = models.JSONField(null=True, blank=True)
    origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.SYSTEM)
    manual_locked = models.BooleanField(default=False)
    revision = models.PositiveBigIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="updated_document_fields",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("business_field_key",)
        constraints = [
            models.UniqueConstraint(
                fields=("document", "business_field_key"),
                name="unique_document_business_field",
            )
        ]


class PhotoDocumentItem(models.Model):
    class Origin(models.TextChoices):
        AI = "ai", "AI"
        MANUAL = "manual", "人工"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="photo_items"
    )
    sequence = models.PositiveIntegerField()
    key_frame_id = models.UUIDField(null=True, blank=True)
    source_material = models.ForeignKey(
        "materials.Material",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="photo_document_items",
    )
    source_deleted = models.BooleanField(default=False)
    source_label = models.CharField(max_length=255, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True)
    reflected_content = models.TextField(blank=True)
    reflected_content_origin = models.CharField(
        max_length=16, choices=Origin.choices, default=Origin.AI
    )
    reflected_content_manual_locked = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    description_origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.AI)
    description_manual_locked = models.BooleanField(default=False)
    revision = models.PositiveBigIntegerField(default=1)

    class Meta:
        ordering = ("sequence", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "sequence"), name="unique_photo_document_sequence"
            ),
            models.UniqueConstraint(
                fields=("document", "source_material"),
                condition=Q(source_material__isnull=False),
                name="unique_photo_material_per_document",
            ),
            models.UniqueConstraint(
                fields=("document", "key_frame_id"),
                condition=Q(key_frame_id__isnull=False),
                name="unique_key_frame_per_document",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        source_deleted=False,
                        source_material__isnull=False,
                        key_frame_id__isnull=True,
                    )
                    | Q(
                        source_deleted=False,
                        source_material__isnull=True,
                        key_frame_id__isnull=False,
                    )
                    | Q(
                        source_deleted=True,
                        source_material__isnull=True,
                        key_frame_id__isnull=True,
                    )
                ),
                name="photo_item_has_one_live_or_deleted_source",
            ),
        ]


class InterviewQaItem(models.Model):
    class Role(models.TextChoices):
        QUESTION = "question", "问"
        ANSWER = "answer", "答"
        UNKNOWN = "unknown", "未知"

    class Origin(models.TextChoices):
        AI = "ai", "AI"
        MANUAL = "manual", "人工"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="qa_items"
    )
    sequence = models.PositiveIntegerField()
    text = models.TextField()
    suggested_role = models.CharField(max_length=16, choices=Role.choices, default=Role.UNKNOWN)
    confirmed_role = models.CharField(max_length=16, choices=Role.choices, null=True, blank=True)
    content_origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.AI)
    content_manual_locked = models.BooleanField(default=False)
    role_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="confirmed_document_qa_roles",
    )
    role_confirmed_at = models.DateTimeField(null=True, blank=True)
    source_segment_start_id = models.UUIDField(null=True, blank=True)
    source_segment_end_id = models.UUIDField(null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=1)

    class Meta:
        ordering = ("sequence", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "sequence"), name="unique_interview_qa_sequence"
            )
        ]


class DocumentFactDependency(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="fact_dependencies"
    )
    fact_id = models.UUIDField()
    fact_revision = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("document", "fact_id"), name="unique_document_fact_dependency"
            )
        ]
        indexes = [models.Index(fields=("fact_id",))]


class DocumentRevision(models.Model):
    class EventType(models.TextChoices):
        GENERATED = "generated", "生成"
        EDITED = "edited", "编辑"
        CONFIRMED = "confirmed", "确认"
        INVALIDATED = "invalidated", "退回初稿"
        EXPORTED = "exported", "导出"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="revisions"
    )
    revision_no = models.PositiveBigIntegerField()
    document_revision_no = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    content_snapshot = models.JSONField(default=dict)
    input_revision_vector = models.JSONField(default=dict)
    mapping_revision = models.ForeignKey(
        "templates.TemplateMappingRevision",
        on_delete=models.PROTECT,
        related_name="document_revisions",
    )
    generator_version = models.CharField(max_length=64)
    generated_storage_key = models.CharField(max_length=500, blank=True)
    generated_sha256 = models.CharField(max_length=64, blank=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    structure_check_result = models.JSONField(default=dict, blank=True)
    render_check_result = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_document_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("document", "revision_no")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "revision_no"),
                name="unique_document_revision_number",
            )
        ]


class DocumentStateEvent(models.Model):
    class Reason(models.TextChoices):
        USER_CONFIRMED = "USER_CONFIRMED", "用户确认"
        SHARED_FIELDS_CHANGED = "SHARED_FIELDS_CHANGED", "检查共享字段已变更"
        REFERENCED_FACT_CHANGED = "REFERENCED_FACT_CHANGED", "引用事实已变更"
        PHOTO_CONTENT_CHANGED = "PHOTO_CONTENT_CHANGED", "照片内容已变更"
        QA_BODY_CHANGED = "QA_BODY_CHANGED", "问答正文已变更"
        TEMPLATE_MAPPING_CHANGED = "TEMPLATE_MAPPING_CHANGED", "模板映射已变更"
        DOCUMENT_FIELD_EDITED = "DOCUMENT_FIELD_EDITED", "文书字段已变更"
        SOURCE_DELETED = "SOURCE_DELETED", "来源已删除"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="state_events"
    )
    from_state = models.CharField(max_length=16, choices=InspectionDocument.State.choices)
    to_state = models.CharField(max_length=16, choices=InspectionDocument.State.choices)
    reason_code = models.CharField(max_length=40, choices=Reason.choices)
    reason_summary = models.CharField(max_length=500)
    caused_by_type = models.CharField(max_length=64)
    caused_by_id = models.UUIDField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="caused_document_state_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("document", "created_at"))]


class DocumentExport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        InspectionDocument, on_delete=models.CASCADE, related_name="exports"
    )
    document_revision = models.ForeignKey(
        DocumentRevision, on_delete=models.PROTECT, related_name="exports"
    )
    storage_key = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    page_count = models.PositiveIntegerField()
    structure_check_result = models.JSONField(default=dict)
    render_check_result = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_document_exports",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "document_revision"),
                name="unique_export_per_generated_revision",
            )
        ]
