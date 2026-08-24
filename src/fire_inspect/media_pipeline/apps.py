from django.apps import AppConfig


class MediaPipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.media_pipeline"
    label = "media_pipeline"

    def ready(self) -> None:
        from fire_inspect.media_pipeline import handlers  # noqa: F401
