from __future__ import annotations

import uuid

from django.db import models


class MaterialVisualAnalysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    material = models.ForeignKey(
        "materials.Material",
        on_delete=models.CASCADE,
        related_name="visual_analyses",
    )
    input_revision = models.PositiveBigIntegerField()
    descriptions = models.JSONField(default=list)
    provider_name = models.CharField(max_length=100)
    model_fingerprint = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("material", "input_revision")
        constraints = [
            models.UniqueConstraint(
                fields=("material", "input_revision"),
                name="unique_visual_analysis_material_revision",
            )
        ]
