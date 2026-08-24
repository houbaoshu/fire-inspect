from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Inspection(models.Model):
    class Type(models.TextChoices):
        MASS_EVENT_PRECHECK = "mass_event_precheck", "大型群众性活动举办前的检查"
        CONSTRUCTION_SITE = "construction_site", "建设工程施工现场检查"
        COMPLAINT_VERIFICATION = "complaint_verification", "对举报投诉的核查"
        REINSPECTION = "reinspection", "复查"
        RESUME_OPERATION = "resume_operation", "申请恢复施工、使用、生产、经营的检查"
        LIFT_SEAL = "lift_seal", "申请解除临时查封的检查"
        OTHER = "other", "其它检查"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_inspections",
    )
    unit_name = models.CharField(max_length=300)
    unit_address = models.CharField(max_length=500, blank=True)
    responsible_person = models.CharField(max_length=100, blank=True)
    contact_name = models.CharField(max_length=100, blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    inspection_at = models.DateTimeField()
    inspection_type = models.CharField(max_length=32, choices=Type.choices)
    general_notes = models.TextField(blank=True)
    shared_revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-inspection_at", "-created_at")
        indexes = [models.Index(fields=("owner", "-inspection_at"))]

    def __str__(self) -> str:
        return self.unit_name


class InspectionPersonnel(models.Model):
    class Role(models.TextChoices):
        INSPECTOR = "inspector", "检查人员"
        RECORDER = "recorder", "记录人员"
        OTHER = "other", "其他人员"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="personnel")
    sequence = models.PositiveIntegerField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inspection_assignments",
    )
    name = models.CharField(max_length=100)
    work_unit = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.INSPECTOR)

    class Meta:
        ordering = ("sequence", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("inspection", "sequence"), name="unique_inspection_personnel_sequence"
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=0), name="inspection_personnel_sequence_nonnegative"
            ),
        ]
