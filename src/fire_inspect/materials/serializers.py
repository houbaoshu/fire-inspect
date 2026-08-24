from rest_framework import serializers

from fire_inspect.materials.models import Material


class MaterialSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    inspection_id = serializers.UUIDField(format="hex_verbose", read_only=True)
    playback_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    steps = serializers.SerializerMethodField()

    class Meta:
        model = Material
        fields = (
            "id",
            "inspection_id",
            "kind",
            "display_name",
            "mime_type",
            "extension",
            "sha256",
            "byte_size",
            "duration_ms",
            "width",
            "height",
            "container_rotation",
            "display_rotation_override",
            "revision",
            "playback_url",
            "thumbnail_url",
            "steps",
            "created_at",
        )

    def get_playback_url(self, instance: Material) -> str | None:
        if instance.kind not in {Material.Kind.VIDEO, Material.Kind.AUDIO}:
            return None
        return f"/api/v1/materials/{instance.id}/play/"

    def get_thumbnail_url(self, instance: Material) -> str | None:
        if instance.kind == Material.Kind.IMAGE:
            return f"/api/v1/materials/{instance.id}/preview/"
        derivative = (
            instance.derivatives.filter(kind__in=("thumbnail", "keyframe"))
            .order_by("-created_at", "-id")
            .first()
        )
        if derivative is None:
            return None
        return f"/api/v1/material-derivatives/{derivative.id}/preview/"

    def get_steps(self, instance: Material) -> list[dict[str, object]]:
        from fire_inspect.jobs.material_steps import material_steps_for
        from fire_inspect.jobs.models import ProcessingJob

        latest: dict[str, ProcessingJob] = {}
        jobs = ProcessingJob.objects.filter(
            scope_type="material",
            scope_id=instance.id,
            inspection_id=instance.inspection_id,
            task_type__in=material_steps_for(instance.kind),
        ).order_by("-created_at", "-id")
        for job in jobs:
            latest.setdefault(job.task_type, job)
        return [
            {
                "name": job.task_type,
                "status": job.status,
                "job_id": str(job.id),
                "safe_error_message": job.safe_error_message or None,
            }
            for job in latest.values()
        ]
