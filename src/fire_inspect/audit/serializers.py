from rest_framework import serializers

from fire_inspect.audit.models import AuditEvent, DomainChangeHistory


class AuditEventSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    object_id = serializers.UUIDField(format="hex_verbose", read_only=True, allow_null=True)
    inspection_id_snapshot = serializers.UUIDField(
        format="hex_verbose", read_only=True, allow_null=True
    )

    class Meta:
        model = AuditEvent
        fields = (
            "id",
            "actor_label",
            "occurred_at",
            "object_type",
            "object_id",
            "inspection_id_snapshot",
            "action",
            "reason",
            "masked_change_summary",
            "request_id",
            "previous_hash",
            "event_hash",
        )


class DomainChangeHistorySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    actor = serializers.CharField(source="actor.username", allow_null=True, read_only=True)

    class Meta:
        model = DomainChangeHistory
        fields = (
            "id",
            "actor",
            "object_type",
            "object_id",
            "action",
            "old_values",
            "new_values",
            "reason",
            "created_at",
        )
