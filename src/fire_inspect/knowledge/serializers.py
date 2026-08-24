from __future__ import annotations

from rest_framework import serializers

from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.serializers import ProcessingJobSerializer
from fire_inspect.knowledge.django_models import KnowledgeDocumentRecord
from fire_inspect.knowledge.services import create_knowledge_document


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    source_label = serializers.SerializerMethodField()
    clause_count = serializers.IntegerField(source="clauses.count", read_only=True)
    processing_job = serializers.SerializerMethodField()

    class Meta:
        model = KnowledgeDocumentRecord
        fields = (
            "id",
            "title",
            "category",
            "issuer",
            "source_reference",
            "version",
            "publication_date",
            "source_verification",
            "source_label",
            "validity",
            "mime_type",
            "sha256",
            "byte_size",
            "processing_status",
            "safe_error_message",
            "processing_job",
            "revision",
            "clause_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_source_label(self, instance) -> str:
        return (
            "已核验"
            if instance.source_verification == KnowledgeDocumentRecord.SourceVerification.VERIFIED
            else "待核验"
        )

    def get_processing_job(self, instance):
        job = getattr(instance, "_processing_job", None)
        if job is None:
            job = (
                ProcessingJob.objects.filter(
                    task_type=ProcessingJob.TaskType.KNOWLEDGE_PARSE,
                    scope_type="knowledge_document",
                    scope_id=instance.id,
                )
                .order_by("-created_at", "-id")
                .first()
            )
        return ProcessingJobSerializer(job).data if job is not None else None


class KnowledgeUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(max_length=300)
    category = serializers.ChoiceField(choices=KnowledgeDocumentRecord.Category.choices)
    declared_internal = serializers.BooleanField(default=False, required=False)
    issuer = serializers.CharField(max_length=300, required=False, allow_blank=True)
    source_reference = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    version = serializers.CharField(max_length=100, required=False, allow_blank=True)
    publication_date = serializers.DateField(required=False, allow_null=True)
    source_verification = serializers.ChoiceField(
        choices=KnowledgeDocumentRecord.SourceVerification.choices,
        default=KnowledgeDocumentRecord.SourceVerification.PENDING,
        required=False,
    )
    validity = serializers.ChoiceField(
        choices=KnowledgeDocumentRecord.Validity.choices,
        default=KnowledgeDocumentRecord.Validity.UNKNOWN,
        required=False,
    )

    def create(self, validated_data):
        upload = validated_data.pop("file")
        record, _ = create_knowledge_document(
            actor=self.context["request"].user,
            upload=upload,
            metadata=validated_data,
            idempotency_key=self.context["request"].headers.get("Idempotency-Key", "").strip()
            or None,
        )
        return record


class KnowledgeMetadataUpdateSerializer(serializers.ModelSerializer):
    declared_internal = serializers.BooleanField(write_only=True, required=False)
    expected_revision = serializers.IntegerField(write_only=True)

    class Meta:
        model = KnowledgeDocumentRecord
        fields = (
            "title",
            "category",
            "declared_internal",
            "issuer",
            "source_reference",
            "version",
            "publication_date",
            "source_verification",
            "validity",
            "expected_revision",
        )

    def validate(self, attrs):
        instance = self.instance
        if attrs["expected_revision"] != instance.revision:
            raise serializers.ValidationError(
                {"expected_revision": "文档已被其他操作修改，请刷新后重试。"}
            )
        category = attrs.get("category", instance.category)
        declared_internal = attrs.pop(
            "declared_internal", instance.category == KnowledgeDocumentRecord.Category.INTERNAL
        )
        if declared_internal and category != KnowledgeDocumentRecord.Category.INTERNAL:
            raise serializers.ValidationError({"category": "内部制度只能归类为内部制度。"})
        verification = attrs.get("source_verification", instance.source_verification)
        validity = attrs.get("validity", instance.validity)
        issuer = attrs.get("issuer", instance.issuer)
        source_reference = attrs.get("source_reference", instance.source_reference)
        if verification == KnowledgeDocumentRecord.SourceVerification.VERIFIED and (
            not issuer.strip() or not source_reference.strip()
        ):
            raise serializers.ValidationError("核验来源需要发布机关和正式来源。")
        if validity != KnowledgeDocumentRecord.Validity.UNKNOWN and (
            verification != KnowledgeDocumentRecord.SourceVerification.VERIFIED
        ):
            raise serializers.ValidationError("来源未核验时不能声明现行或失效。")
        return attrs

    def update(self, instance, validated_data):
        validated_data.pop("expected_revision")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.revision += 1
        instance.save()
        return instance
