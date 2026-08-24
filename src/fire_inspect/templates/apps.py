from django.apps import AppConfig


class TemplatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.templates"

    def ready(self) -> None:
        from fire_inspect.templates import handlers  # noqa: F401
