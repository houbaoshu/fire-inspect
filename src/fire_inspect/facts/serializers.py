from rest_framework import serializers

from fire_inspect.evidence.serializers import serialize_fact_evidence
from fire_inspect.facts.models import Fact, FactRevision


class FactSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    inspection_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    primary_source_material_id = serializers.UUIDField(
        format="hex_verbose", read_only=True, allow_null=True
    )
    confirmed_by = serializers.CharField(
        source="confirmed_by.username", read_only=True, allow_null=True
    )
    created_by = serializers.CharField(
        source="created_by.username", read_only=True, allow_null=True
    )
    evidence = serializers.SerializerMethodField()

    class Meta:
        model = Fact
        fields = (
            "id",
            "inspection_id",
            "content",
            "review_state",
            "needs_verification",
            "verification_note",
            "origin",
            "primary_source_material_id",
            "revision",
            "confirmed_by",
            "confirmed_at",
            "created_by",
            "evidence",
            "created_at",
            "updated_at",
        )

    def get_evidence(self, instance: Fact) -> list[dict[str, object]]:
        return serialize_fact_evidence(instance)


class FactRevisionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    actor = serializers.CharField(source="actor.username", read_only=True, allow_null=True)

    class Meta:
        model = FactRevision
        fields = ("id", "revision", "action", "snapshot", "actor", "created_at")
