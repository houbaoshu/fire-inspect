from rest_framework import serializers

from fire_inspect.jobs.models import ProcessingJob


class ProcessingJobSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    scope_id = serializers.UUIDField(format="hex_verbose", read_only=True, allow_null=True)
    inspection_id = serializers.UUIDField(format="hex_verbose", read_only=True, allow_null=True)

    class Meta:
        model = ProcessingJob
        fields = (
            "id",
            "task_type",
            "scope_type",
            "scope_id",
            "inspection_id",
            "input_fingerprint",
            "status",
            "attempt_count",
            "max_attempts",
            "available_at",
            "safe_error_code",
            "safe_error_message",
            "created_at",
            "started_at",
            "finished_at",
        )
