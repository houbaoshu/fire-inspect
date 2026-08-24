from django.apps import AppConfig


class AIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.ai"
    label = "ai"

    def ready(self) -> None:
        from fire_inspect.ai import handlers  # noqa: F401
