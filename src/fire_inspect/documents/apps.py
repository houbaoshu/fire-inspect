from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.documents"

    def ready(self) -> None:
        from fire_inspect.documents import handlers, receivers  # noqa: F401
