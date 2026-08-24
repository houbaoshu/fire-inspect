from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ProcessingJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "处理中"
        SUCCEEDED = "succeeded", "成功"
        FAILED = "failed", "失败"
        CANCELLED = "cancelled", "已取消"
        SUPERSEDED = "superseded", "已过期"

    class TaskType(models.TextChoices):
        TRANSCODE = "transcode", "兼容转码"
        AUDIO_EXTRACT = "audio_extract", "音频抽取"
        ASR = "asr", "语音转写"
        KEYFRAMES = "keyframes", "关键画面"
        VISION = "vision", "视觉描述"
        FACT_EXTRACTION = "fact_extraction", "事实提取"
        KNOWLEDGE_PARSE = "knowledge_parse", "知识解析"
        EMBEDDING = "embedding", "向量索引"
        TEMPLATE_RECOGNITION = "template_recognition", "模板识别"
        DOCUMENT_GENERATION = "document_generation", "文书生成"
        DOCUMENT_RENDER = "document_render", "文书渲染"
        DELETION_CLEANUP = "deletion_cleanup", "删除清理"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_type = models.CharField(max_length=40, choices=TaskType.choices)
    scope_type = models.CharField(max_length=40)
    scope_id = models.UUIDField(null=True, blank=True)
    inspection = models.ForeignKey(
        "inspections.Inspection",
        on_delete=models.CASCADE,
        related_name="processing_jobs",
        null=True,
        blank=True,
    )
    input_fingerprint = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    available_at = models.DateTimeField()
    lease_owner = models.CharField(max_length=200, blank=True)
    lease_token = models.CharField(max_length=128, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    expected_scope_revision = models.PositiveBigIntegerField(null=True, blank=True)
    safe_error_code = models.CharField(max_length=100, blank=True)
    safe_error_message = models.CharField(max_length=500, blank=True)
    last_retry_idempotency_key = models.CharField(
        max_length=200, null=True, blank=True, unique=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("task_type", "scope_id", "input_fingerprint"),
                name="unique_job_task_scope_input",
            ),
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1), name="job_max_attempts_positive"
            ),
        ]
        indexes = [
            models.Index(fields=("status", "available_at", "created_at")),
            models.Index(fields=("inspection", "created_at")),
        ]


class JobAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(ProcessingJob, on_delete=models.CASCADE, related_name="attempts")
    attempt_no = models.PositiveIntegerField()
    lease_token = models.CharField(max_length=128)
    capability = models.CharField(max_length=40, blank=True)
    provider_name = models.CharField(max_length=100, blank=True)
    model_name = models.CharField(max_length=100, blank=True)
    config_fingerprint = models.CharField(max_length=128, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=30, blank=True)
    safe_error_code = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ("attempt_no",)
        constraints = [
            models.UniqueConstraint(fields=("job", "attempt_no"), name="unique_job_attempt_no")
        ]
