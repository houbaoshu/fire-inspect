from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class InspectionScopedEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey("inspections.Inspection", on_delete=models.CASCADE)
    fact = models.ForeignKey("facts.Fact", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def clean(self):
        if self.fact_id and self.inspection_id and self.fact.inspection_id != self.inspection_id:
            raise ValidationError("证据与事实不属于同一次检查。")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class FactMediaRange(InspectionScopedEvidence):
    fact = models.ForeignKey("facts.Fact", on_delete=models.CASCADE, related_name="media_ranges")
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="fact_media_ranges"
    )
    material = models.ForeignKey(
        "materials.Material", on_delete=models.CASCADE, related_name="fact_media_ranges"
    )
    start_ms = models.PositiveBigIntegerField()
    end_ms = models.PositiveBigIntegerField()
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("material_id", "start_ms", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(end_ms__gt=F("start_ms")), name="fact_media_range_positive"
            ),
            models.UniqueConstraint(
                fields=("fact", "material", "start_ms", "end_ms"),
                name="unique_fact_media_range",
            ),
        ]
        indexes = [models.Index(fields=("inspection", "material", "start_ms"))]

    def clean(self):
        super().clean()
        if self.material_id and self.inspection_id:
            if self.material.inspection_id != self.inspection_id:
                raise ValidationError("证据材料不属于当前检查。")
            if self.start_ms < 0 or self.end_ms <= self.start_ms:
                raise ValidationError("证据时间范围无效。")
            if self.material.duration_ms is None:
                raise ValidationError("材料时长未知，不能关联时间证据。")
            if self.end_ms > self.material.duration_ms + 3_000:
                raise ValidationError("证据时间超过材料时间轴。")


class FactTranscriptLink(InspectionScopedEvidence):
    fact = models.ForeignKey(
        "facts.Fact", on_delete=models.CASCADE, related_name="transcript_links"
    )
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="fact_transcript_links"
    )
    segment = models.ForeignKey(
        "transcripts.TranscriptSegmentRecord",
        on_delete=models.CASCADE,
        related_name="fact_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("fact", "segment"), name="unique_fact_transcript_link")
        ]

    def clean(self):
        super().clean()
        if (
            self.segment_id
            and self.inspection_id
            and self.segment.transcript.material.inspection_id != self.inspection_id
        ):
            raise ValidationError("转写片段不属于当前检查。")


class FactKeyframeLink(InspectionScopedEvidence):
    fact = models.ForeignKey("facts.Fact", on_delete=models.CASCADE, related_name="keyframe_links")
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="fact_keyframe_links"
    )
    derivative = models.ForeignKey(
        "materials.MaterialDerivative",
        on_delete=models.CASCADE,
        related_name="fact_keyframe_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("fact", "derivative"), name="unique_fact_keyframe_link")
        ]

    def clean(self):
        super().clean()
        if self.derivative_id and self.inspection_id:
            if self.derivative.material.inspection_id != self.inspection_id:
                raise ValidationError("关键画面不属于当前检查。")
            if self.derivative.kind != "keyframe":
                raise ValidationError("关联的派生物不是关键画面。")


class FactPhotoLink(InspectionScopedEvidence):
    fact = models.ForeignKey("facts.Fact", on_delete=models.CASCADE, related_name="photo_links")
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="fact_photo_links"
    )
    photo_material = models.ForeignKey(
        "materials.Material", on_delete=models.CASCADE, related_name="fact_photo_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("fact", "photo_material"), name="unique_fact_photo_link"
            )
        ]

    def clean(self):
        super().clean()
        if self.photo_material_id and self.inspection_id:
            if self.photo_material.inspection_id != self.inspection_id:
                raise ValidationError("照片不属于当前检查。")
            if self.photo_material.kind != "image":
                raise ValidationError("关联材料不是照片。")


class FactClauseLink(InspectionScopedEvidence):
    fact = models.ForeignKey("facts.Fact", on_delete=models.CASCADE, related_name="clause_links")
    inspection = models.ForeignKey(
        "inspections.Inspection", on_delete=models.CASCADE, related_name="fact_clause_links"
    )
    clause = models.ForeignKey(
        "knowledge.KnowledgeClauseRecord",
        on_delete=models.PROTECT,
        related_name="fact_links",
    )
    applicability_reason = models.CharField(max_length=1000)
    confirmed_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="confirmed_fact_clause_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("fact", "clause"), name="unique_fact_clause_link")
        ]
