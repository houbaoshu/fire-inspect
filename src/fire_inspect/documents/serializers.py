from __future__ import annotations

from rest_framework import serializers

from .models import (
    DocumentFieldValue,
    InspectionDocument,
    InterviewQaItem,
    PhotoDocumentItem,
)


class DocumentFieldValueSerializer(serializers.ModelSerializer):
    key = serializers.CharField(source="business_field_key", read_only=True)
    value = serializers.JSONField(source="value_json", read_only=True)

    class Meta:
        model = DocumentFieldValue
        fields = ("id", "key", "value", "origin", "manual_locked", "revision", "updated_at")


class PhotoDocumentItemSerializer(serializers.ModelSerializer):
    source_id = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = PhotoDocumentItem
        fields = (
            "id",
            "sequence",
            "source_id",
            "thumbnail_url",
            "source_deleted",
            "source_label",
            "reflected_content",
            "reflected_content_origin",
            "reflected_content_manual_locked",
            "description",
            "description_origin",
            "description_manual_locked",
            "revision",
        )

    def get_source_id(self, instance: PhotoDocumentItem):
        return str(instance.source_material_id or instance.key_frame_id or "") or None

    def get_thumbnail_url(self, instance: PhotoDocumentItem):
        if instance.source_deleted:
            return None
        if instance.source_material_id:
            return f"/api/v1/materials/{instance.source_material_id}/preview/"
        if instance.key_frame_id:
            return f"/api/v1/material-derivatives/{instance.key_frame_id}/preview/"
        return None


class InterviewQaItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InterviewQaItem
        fields = (
            "id",
            "sequence",
            "text",
            "suggested_role",
            "confirmed_role",
            "content_origin",
            "content_manual_locked",
            "revision",
        )


class InspectionDocumentSerializer(serializers.ModelSerializer):
    inspection_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    fields = DocumentFieldValueSerializer(source="field_values", many=True, read_only=True)
    photo_items = PhotoDocumentItemSerializer(many=True, read_only=True)
    qa_items = InterviewQaItemSerializer(many=True, read_only=True)
    generated_page_count = serializers.SerializerMethodField()
    fact_dependency_ids = serializers.SerializerMethodField()

    class Meta:
        model = InspectionDocument
        fields = (
            "id",
            "inspection_id",
            "document_type",
            "name",
            "state",
            "revision",
            "generator_status",
            "generator_job_id",
            "generator_safe_error",
            "generation_valid",
            "invalidation_reason",
            "upgrade_available",
            "fields",
            "photo_items",
            "qa_items",
            "fact_dependency_ids",
            "generated_page_count",
            "created_at",
            "updated_at",
        )

    def get_generated_page_count(self, instance: InspectionDocument):
        revision = instance.last_generated_revision
        return revision.page_count if revision is not None else None

    def get_fact_dependency_ids(self, instance: InspectionDocument):
        return [
            str(value) for value in instance.fact_dependencies.values_list("fact_id", flat=True)
        ]
