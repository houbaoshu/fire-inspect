from __future__ import annotations

from rest_framework import serializers

from fire_inspect.accounts.serializers import CurrentUserSerializer
from fire_inspect.inspections.models import Inspection, InspectionPersonnel


class InspectionPersonnelSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    user_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = InspectionPersonnel
        fields = ("id", "sequence", "user_id", "name", "work_unit", "role")
        read_only_fields = ("sequence",)


class InspectionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    owner = CurrentUserSerializer(read_only=True)
    personnel = InspectionPersonnelSerializer(many=True, required=False)
    shared_revision = serializers.IntegerField(read_only=True)
    processing_summary = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = (
            "id",
            "owner",
            "unit_name",
            "unit_address",
            "responsible_person",
            "contact_name",
            "contact_phone",
            "inspection_at",
            "inspection_type",
            "general_notes",
            "shared_revision",
            "processing_summary",
            "personnel",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate_unit_name(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("被检查单位不能为空。")
        return value

    def validate_contact_phone(self, value: str) -> str:
        value = value.strip()
        if len(value) > 50:
            raise serializers.ValidationError("联系电话过长。")
        return value

    def get_processing_summary(self, instance: Inspection) -> dict[str, int]:
        from django.db.models import Count

        counts = {
            item["status"]: item["total"]
            for item in instance.processing_jobs.values("status").annotate(total=Count("id"))
        }
        return {
            key: int(counts.get(key, 0)) for key in ("queued", "running", "succeeded", "failed")
        }

    def create(self, validated_data):
        from fire_inspect.inspections.services import create_inspection

        personnel = validated_data.pop("personnel", [])
        return create_inspection(
            actor=self.context["request"].user,
            values=validated_data,
            personnel_values=personnel,
        )
