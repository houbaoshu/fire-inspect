from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def template_upload_to(instance: TemplateVersion, filename: str) -> str:
    del filename
    return f"templates/{instance.template_id}/v{instance.version_no}/original.docx"


class WordTemplate(models.Model):
    class Type(models.TextChoices):
        RECORD = "record", "监督检查记录"
        PHOTO = "photo", "照片文书"
        INTERVIEW = "interview", "询问笔录"

    class RecognitionStatus(models.TextChoices):
        QUEUED = "queued", "待识别"
        RUNNING = "running", "识别中"
        SUCCEEDED = "succeeded", "识别成功"
        FAILED = "failed", "识别失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.CharField(max_length=16, choices=Type.choices)
    name = models.CharField(max_length=200)
    active_mapping_revision = models.ForeignKey(
        "TemplateMappingRevision",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="active_for_templates",
    )
    active = models.BooleanField(default=True)
    recognition_status = models.CharField(
        max_length=16,
        choices=RecognitionStatus.choices,
        default=RecognitionStatus.QUEUED,
    )
    safe_error_message = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_word_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("document_type", "name", "created_at")
        indexes = [models.Index(fields=("document_type", "active"))]

    def __str__(self) -> str:
        return self.name

    @property
    def current_mapping_revision(self):
        latest_version = self.versions.order_by("-version_no").first()
        if latest_version is not None:
            draft = (
                latest_version.mapping_revisions.filter(state=TemplateMappingRevision.State.DRAFT)
                .order_by("-revision_no")
                .first()
            )
            if draft is not None:
                return draft
            confirmed = (
                latest_version.mapping_revisions.filter(
                    state=TemplateMappingRevision.State.CONFIRMED
                )
                .order_by("-revision_no")
                .first()
            )
            if confirmed is not None:
                return confirmed
        return None


class TemplateVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(WordTemplate, on_delete=models.PROTECT, related_name="versions")
    version_no = models.PositiveIntegerField()
    original_file = models.FileField(upload_to=template_upload_to, max_length=500)
    original_filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    validation_result = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_template_versions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("template", "version_no")
        constraints = [
            models.UniqueConstraint(
                fields=("template", "version_no"), name="unique_template_version_number"
            )
        ]
        indexes = [models.Index(fields=("sha256",))]

    @property
    def original_storage_key(self) -> str:
        return self.original_file.name

    def save(self, *args, **kwargs):
        if not self._state.adding:
            stored = type(self).objects.get(pk=self.pk)
            immutable = (
                "template_id",
                "version_no",
                "original_filename",
                "sha256",
                "validation_result",
                "created_by_id",
            )
            changed = any(getattr(stored, field) != getattr(self, field) for field in immutable)
            changed = changed or stored.original_file.name != self.original_file.name
            if changed:
                raise ValidationError("模板版本不可修改；请创建新版本。")
        super().save(*args, **kwargs)


class TemplateMappingRevision(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "草稿"
        CONFIRMED = "confirmed", "已确认"
        SUPERSEDED = "superseded", "已替代"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template_version = models.ForeignKey(
        TemplateVersion, on_delete=models.PROTECT, related_name="mapping_revisions"
    )
    revision_no = models.PositiveIntegerField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    recognizer_version = models.CharField(max_length=64)
    warnings = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_template_mappings",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="confirmed_template_mappings",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("template_version", "revision_no")
        constraints = [
            models.UniqueConstraint(
                fields=("template_version", "revision_no"),
                name="unique_template_mapping_revision",
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            stored = type(self).objects.get(pk=self.pk)
            if stored.state == self.State.CONFIRMED:
                immutable_fields = (
                    "template_version_id",
                    "revision_no",
                    "recognizer_version",
                    "warnings",
                    "created_by_id",
                    "confirmed_by_id",
                    "confirmed_at",
                )
                changed = any(
                    getattr(stored, field) != getattr(self, field) for field in immutable_fields
                )
                if changed or self.state not in {self.State.CONFIRMED, self.State.SUPERSEDED}:
                    raise ValidationError("已确认的模板映射不可修改。")
        super().save(*args, **kwargs)


class TemplateRegion(models.Model):
    class RepeatScope(models.TextChoices):
        SINGLE = "single", "单一"
        DOCUMENT_COMMON = "document_common", "文书公共"
        PER_ITEM = "per_item", "逐项"

    class FillStrategy(models.TextChoices):
        LABEL_SUFFIX = "label_suffix", "标签后填充"
        TABLE_CELL = "table_cell", "表格单元格"
        CHECKBOX = "checkbox", "复选框"
        IMAGE_REGION = "image_region", "图片区域"
        BODY = "body", "正文"
        FOOTER_FIELD = "footer_field", "页脚域"
        PRESERVE_BLANK = "preserve_blank", "保持空白"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mapping_revision = models.ForeignKey(
        TemplateMappingRevision, on_delete=models.CASCADE, related_name="regions"
    )
    business_field_key = models.CharField(max_length=100)
    display_name = models.CharField(max_length=100)
    repeat_scope = models.CharField(max_length=32, choices=RepeatScope.choices)
    fill_strategy = models.CharField(max_length=32, choices=FillStrategy.choices)
    locator = models.JSONField(default=dict)
    style_policy = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(default=0)
    enabled = models.BooleanField(default=True)
    required = models.BooleanField(default=True)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sequence", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("mapping_revision", "business_field_key"),
                name="unique_template_region_key",
            )
        ]

    def clean(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValidationError({"confidence": "置信度必须在 0 到 1 之间。"})
        if (
            "signature" in self.business_field_key
            and self.fill_strategy != self.FillStrategy.PRESERVE_BLANK
        ):
            raise ValidationError({"fill_strategy": "签名区域必须保持空白。"})

    def save(self, *args, **kwargs):
        if self.mapping_revision_id:
            state = (
                TemplateMappingRevision.objects.only("state").get(pk=self.mapping_revision_id).state
            )
            if state != TemplateMappingRevision.State.DRAFT:
                raise ValidationError("已确认的模板映射区域不可修改。")
        self.full_clean()
        super().save(*args, **kwargs)
