from rest_framework import serializers

from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord


class TranscriptSegmentSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    role_confirmed_by = serializers.CharField(
        source="role_confirmed_by.username", allow_null=True, read_only=True
    )

    class Meta:
        model = TranscriptSegmentRecord
        fields = (
            "id",
            "sequence",
            "start_ms",
            "end_ms",
            "text",
            "suggested_role",
            "confirmed_role",
            "text_origin",
            "role_confirmed_by",
            "role_confirmed_at",
            "revision",
        )


class TranscriptSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    material_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    material_name = serializers.CharField(source="material.display_name", read_only=True)
    segments = TranscriptSegmentSerializer(many=True, read_only=True)

    class Meta:
        model = Transcript
        fields = (
            "id",
            "material_id",
            "material_name",
            "full_text",
            "full_text_origin",
            "language",
            "revision",
            "segments",
            "created_at",
            "updated_at",
        )
