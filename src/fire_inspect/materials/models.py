from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models


def original_upload_to(instance: Material, filename: str) -> str:
    return f"originals/inspections/{instance.inspection_id}/{instance.id}/original"


class MaterialManager(models.Manager):
    def create_text(self, *, inspection, created_by, display_name: str, text: str) -> Material:
        raw = text.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.filter(inspection=inspection, sha256=digest).first()
        if existing:
            return existing
        material = self.model(
            inspection=inspection,
            kind=Material.Kind.TEXT,
            display_name=display_name,
            mime_type="text/plain",
            extension=".txt",
            sha256=digest,
            byte_size=len(raw),
            created_by=created_by,
        )
        material.original_file.save("original", ContentFile(raw), save=False)
        material.save(force_insert=True)
        return material


class Material(models.Model):
    class Kind(models.TextChoices):
        VIDEO = "video", "视频"
        IMAGE = "image", "图片"
        AUDIO = "audio", "录音"
        TEXT = "text", "文字说明"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="materials"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    display_name = models.CharField(max_length=255)
    original_file = models.FileField(upload_to=original_upload_to, max_length=500)
    mime_type = models.CharField(max_length=100)
    extension = models.CharField(max_length=16)
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    duration_ms = models.PositiveBigIntegerField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    codec_info = models.JSONField(default=dict, blank=True)
    container_rotation = models.SmallIntegerField(default=0)
    display_rotation_override = models.SmallIntegerField(null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_materials",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MaterialManager()

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("inspection", "sha256"), name="unique_material_content_per_inspection"
            )
        ]
        indexes = [models.Index(fields=("inspection", "created_at"))]


class MaterialDerivative(models.Model):
    class Kind(models.TextChoices):
        PLAYBACK = "playback", "播放副本"
        AUDIO_EXTRACT = "audio_extract", "音频抽取"
        KEYFRAME = "keyframe", "关键画面"
        THUMBNAIL = "thumbnail", "缩略图"
        PREVIEW = "preview", "预览"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="derivatives")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    storage_key = models.CharField(max_length=500)
    mime_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    source_timestamp_ms = models.PositiveBigIntegerField(null=True, blank=True)
    timeline_offset_ms = models.BigIntegerField(default=0)
    source_job_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
