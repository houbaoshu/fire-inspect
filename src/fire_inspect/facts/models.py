from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Fact(models.Model):
    class ReviewState(models.TextChoices):
        CANDIDATE = "candidate", "候选"
        CONFIRMED = "confirmed", "已确认"
        WITHDRAWN = "withdrawn", "已撤回"

    class Origin(models.TextChoices):
        AI = "ai", "AI"
        MANUAL = "manual", "人工"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="facts"
    )
    content = models.TextField()
    review_state = models.CharField(
        max_length=16, choices=ReviewState.choices, default=ReviewState.CANDIDATE
    )
    needs_verification = models.BooleanField(default=False)
    verification_note = models.CharField(max_length=1000, blank=True)
    origin = models.CharField(max_length=10, choices=Origin.choices)
    primary_source_material = models.ForeignKey(
        "materials.Material",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_source_facts",
    )
    source_input_revision = models.PositiveBigIntegerField(null=True, blank=True)
    normalized_content_hash = models.CharField(max_length=64, null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=1)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_facts",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_facts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "primary_source_material",
                    "source_input_revision",
                    "normalized_content_hash",
                ),
                name="unique_ai_fact_input_content",
            )
        ]
        indexes = [
            models.Index(fields=("inspection", "review_state", "created_at")),
        ]


class FactRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fact = models.ForeignKey(Fact, on_delete=models.CASCADE, related_name="revisions")
    revision = models.PositiveBigIntegerField()
    action = models.CharField(max_length=50)
    snapshot = models.JSONField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fact_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("revision", "created_at", "id")
        constraints = [
            models.UniqueConstraint(fields=("fact", "revision"), name="unique_fact_revision")
        ]
