from __future__ import annotations

from rest_framework import serializers

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.serializers import ProcessingJobSerializer

from .models import TemplateMappingRevision, TemplateRegion, TemplateVersion, WordTemplate


class TemplateRegionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    location_preview = serializers.SerializerMethodField()
    warning = serializers.SerializerMethodField()

    class Meta:
        model = TemplateRegion
        fields = (
            "id",
            "display_name",
            "repeat_scope",
            "fill_strategy",
            "confidence",
            "enabled",
            "required",
            "sequence",
            "location_preview",
            "warning",
        )

    def get_location_preview(self, instance: TemplateRegion) -> str:
        anchor = str(instance.locator.get("anchor", "")).replace("|", " / ").strip()
        return anchor[:160] or "管理员人工定位区域"

    def get_warning(self, instance: TemplateRegion) -> str:
        if instance.confidence < 0.55:
            return "置信度较低，需管理员人工确认。"
        return ""


class TemplateMappingSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    regions = TemplateRegionSerializer(many=True, read_only=True)

    class Meta:
        model = TemplateMappingRevision
        fields = ("id", "revision_no", "state", "recognizer_version", "warnings", "regions")


class TemplateVersionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)

    class Meta:
        model = TemplateVersion
        fields = ("id", "version_no", "original_filename", "created_at")


class WordTemplateSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    mapping = serializers.SerializerMethodField()
    versions = TemplateVersionSerializer(many=True, read_only=True)
    recognition_job = serializers.SerializerMethodField()

    class Meta:
        model = WordTemplate
        fields = (
            "id",
            "name",
            "document_type",
            "active",
            "recognition_status",
            "safe_error_message",
            "recognition_job",
            "mapping",
            "versions",
            "created_at",
        )

    def get_mapping(self, instance: WordTemplate):
        mapping = instance.current_mapping_revision
        if mapping is None:
            return None
        return TemplateMappingSerializer(mapping).data

    def get_recognition_job(self, instance: WordTemplate):
        job = getattr(instance, "_recognition_job", None)
        if job is None:
            job = (
                ProcessingJob.objects.filter(
                    task_type=ProcessingJob.TaskType.TEMPLATE_RECOGNITION,
                    scope_type="word_template",
                    scope_id=instance.id,
                )
                .order_by("-created_at", "-id")
                .first()
            )
        return ProcessingJobSerializer(job).data if job is not None else None
