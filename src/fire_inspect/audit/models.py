from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.utils import timezone

from fire_inspect.operations.redaction import redact_text

PHONE_PATTERN = re.compile(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)")
IDENTITY_PATTERN = re.compile(r"(?<!\w)(\d{6})\d{8,11}([\dXx]{4})(?!\w)")
_AUDIT_APPEND_LOCK = threading.RLock()
_POSTGRES_AUDIT_LOCK_ID = 0x4649524541554449


def mask_sensitive_text(value: str) -> str:
    value = redact_text(
        value,
        sensitive_roots=(
            str(getattr(settings, "BASE_DIR", "")),
            str(getattr(settings, "MEDIA_ROOT", "")),
            str(getattr(settings, "FIRE_INSPECT_STAGING_ROOT", "")),
            str(getattr(settings, "FIRE_INSPECT_QUARANTINE_ROOT", "")),
        ),
    )
    value = PHONE_PATTERN.sub(r"\1****\2", value)
    return IDENTITY_PATTERN.sub(r"\1********\2", value)


def masked_change_summary(changes: dict[str, Any]) -> str:
    compact = json.dumps(changes, ensure_ascii=False, sort_keys=True, default=str)
    return mask_sensitive_text(compact)[:1000]


class DomainChangeHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        "inspections.Inspection",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="domain_history",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="domain_changes",
    )
    object_type = models.CharField(max_length=64)
    object_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=64)
    old_values = models.JSONField(default=dict)
    new_values = models.JSONField(default=dict)
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=("inspection", "created_at")),
            models.Index(fields=("object_type", "object_id", "created_at")),
        ]


class AppendOnlyAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("审计记录不可修改。")

    def delete(self):
        raise ValidationError("审计记录不可删除。")


class AuditEventManager(models.Manager.from_queryset(AppendOnlyAuditQuerySet)):
    @transaction.atomic
    def append(
        self,
        *,
        actor,
        object_type: str,
        object_id: uuid.UUID | None,
        action: str,
        masked_change_summary: str,
        inspection_id_snapshot: uuid.UUID | None = None,
        reason: str = "",
        request_id: str = "",
    ) -> AuditEvent:
        with _AUDIT_APPEND_LOCK:
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_POSTGRES_AUDIT_LOCK_ID])
                # The transaction-scoped advisory lock serializes appenders without
                # requiring UPDATE permission on the append-only audit table.
                previous = self.order_by("-occurred_at", "-id").first()
            else:
                previous = self.select_for_update().order_by("-occurred_at", "-id").first()
            previous_hash = previous.event_hash if previous else ""
            event = self.model(
                actor=actor if getattr(actor, "is_authenticated", False) else None,
                actor_label=getattr(actor, "username", "system") or "system",
                object_type=object_type,
                object_id=object_id,
                inspection_id_snapshot=inspection_id_snapshot,
                action=action,
                reason=mask_sensitive_text(reason)[:500],
                masked_change_summary=mask_sensitive_text(masked_change_summary)[:1000],
                request_id=request_id[:64],
                previous_hash=previous_hash,
            )
            event.event_hash = event.calculate_hash()
            event.save(force_insert=True)
            return event


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_label = models.CharField(max_length=150)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    object_type = models.CharField(max_length=64)
    object_id = models.UUIDField(null=True, blank=True)
    inspection_id_snapshot = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=64)
    reason = models.CharField(max_length=500, blank=True)
    masked_change_summary = models.CharField(max_length=1000, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64, unique=True)

    objects = AuditEventManager()

    class Meta:
        ordering = ("occurred_at", "id")
        indexes = [
            models.Index(fields=("object_type", "object_id", "occurred_at")),
            models.Index(fields=("inspection_id_snapshot", "occurred_at")),
        ]

    @classmethod
    def append(cls, **kwargs) -> AuditEvent:
        return cls.objects.append(**kwargs)

    def calculate_hash(self) -> str:
        payload = {
            "id": str(self.id),
            "actor_label": self.actor_label,
            "occurred_at": self.occurred_at.isoformat(),
            "object_type": self.object_type,
            "object_id": str(self.object_id or ""),
            "inspection_id_snapshot": str(self.inspection_id_snapshot or ""),
            "action": self.action,
            "reason": self.reason,
            "masked_change_summary": self.masked_change_summary,
            "request_id": self.request_id,
            "previous_hash": self.previous_hash,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("审计记录不可修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("审计记录不可删除。")
