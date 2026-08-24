from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


def knowledge_upload_path(instance: KnowledgeDocumentRecord, filename: str) -> str:
    extension = ".pdf" if filename.lower().endswith(".pdf") else ".docx"
    return f"knowledge/{instance.id}/1/original{extension}"


class KnowledgeDocumentRecord(models.Model):
    class Category(models.TextChoices):
        LAW = "law", "正式法律法规"
        STANDARD = "standard", "标准规范"
        LOCAL_RULE = "local_rule", "地方规定"
        INTERNAL = "internal", "内部制度"
        OTHER = "other", "其他材料"

    class SourceVerification(models.TextChoices):
        PENDING = "pending", "待核验"
        VERIFIED = "verified", "已核验"
        REJECTED = "rejected", "已拒绝"

    class Validity(models.TextChoices):
        UNKNOWN = "unknown", "未知"
        EFFECTIVE = "effective", "现行有效"
        EXPIRED = "expired", "已失效"

    class ProcessingStatus(models.TextChoices):
        PENDING = "pending", "待处理"
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=300)
    category = models.CharField(max_length=20, choices=Category.choices)
    issuer = models.CharField(max_length=300, blank=True)
    source_reference = models.CharField(max_length=1000, blank=True)
    version = models.CharField(max_length=100, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    source_verification = models.CharField(
        max_length=16,
        choices=SourceVerification.choices,
        default=SourceVerification.PENDING,
    )
    validity = models.CharField(
        max_length=16,
        choices=Validity.choices,
        default=Validity.UNKNOWN,
    )
    original_file = models.FileField(upload_to=knowledge_upload_path)
    mime_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64, unique=True)
    byte_size = models.PositiveBigIntegerField()
    processing_status = models.CharField(
        max_length=16,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    safe_error_message = models.CharField(max_length=500, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_knowledge_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "knowledge"
        ordering = ("title", "created_at")


class KnowledgeClauseRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        KnowledgeDocumentRecord,
        on_delete=models.CASCADE,
        related_name="clauses",
    )
    sequence = models.PositiveIntegerField()
    chapter = models.CharField(max_length=200, blank=True)
    section = models.CharField(max_length=200, blank=True)
    clause_identifier = models.CharField(max_length=100)
    heading = models.CharField(max_length=300, blank=True)
    content = models.TextField()
    page_start = models.PositiveIntegerField()
    page_end = models.PositiveIntegerField()
    source_locator = models.JSONField(default=dict)
    normalized_text = models.TextField()
    parser_version = models.CharField(max_length=50, default="coordinate-v1")

    class Meta:
        app_label = "knowledge"
        ordering = ("document", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "sequence"),
                name="knowledge_document_clause_sequence_unique",
            )
        ]
        indexes = [
            models.Index(fields=("document", "clause_identifier")),
        ]


class KnowledgeEmbeddingRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待处理"
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失败"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clause = models.ForeignKey(
        KnowledgeClauseRecord,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )
    model_fingerprint = models.CharField(max_length=200)
    input_revision = models.PositiveBigIntegerField(default=1)
    dimensions = models.PositiveIntegerField()
    values = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    safe_error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "knowledge"
        constraints = [
            models.UniqueConstraint(
                fields=("clause", "model_fingerprint"),
                name="knowledge_clause_embedding_model_unique",
            )
        ]
