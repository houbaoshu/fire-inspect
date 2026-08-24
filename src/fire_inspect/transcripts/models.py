from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Transcript(models.Model):
    class TextOrigin(models.TextChoices):
        AI = "ai", "AI"
        MANUAL = "manual", "人工"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material = models.OneToOneField(
        "materials.Material", on_delete=models.CASCADE, related_name="transcript"
    )
    full_text = models.TextField()
    full_text_origin = models.CharField(
        max_length=10, choices=TextOrigin.choices, default=TextOrigin.AI
    )
    language = models.CharField(max_length=20, default="zh-CN")
    revision = models.PositiveBigIntegerField(default=1)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_transcripts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")


class TranscriptSegmentRecord(models.Model):
    class SemanticRole(models.TextChoices):
        QUESTION = "question", "问"
        ANSWER = "answer", "答"
        UNKNOWN = "unknown", "未知"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transcript = models.ForeignKey(Transcript, on_delete=models.CASCADE, related_name="segments")
    sequence = models.PositiveIntegerField()
    start_ms = models.PositiveBigIntegerField()
    end_ms = models.PositiveBigIntegerField()
    text = models.TextField()
    suggested_role = models.CharField(
        max_length=10, choices=SemanticRole.choices, default=SemanticRole.UNKNOWN
    )
    confirmed_role = models.CharField(
        max_length=10, choices=SemanticRole.choices, null=True, blank=True
    )
    text_origin = models.CharField(
        max_length=10, choices=Transcript.TextOrigin.choices, default=Transcript.TextOrigin.AI
    )
    role_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_transcript_roles",
    )
    role_confirmed_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=1)

    class Meta:
        ordering = ("sequence", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("transcript", "sequence"), name="unique_transcript_segment_sequence"
            ),
            models.CheckConstraint(
                condition=Q(end_ms__gt=F("start_ms")), name="transcript_segment_positive_range"
            ),
        ]
        indexes = [models.Index(fields=("transcript", "start_ms"))]
