from importlib import import_module

from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.knowledge"
    label = "knowledge"

    def import_models(self) -> None:
        # Keep pure parser dataclasses in knowledge.models importable without a
        # configured Django environment while still registering persistence
        # models when Django starts.
        super().import_models()
        import_module("fire_inspect.knowledge.django_models")

    def ready(self) -> None:
        import_module("fire_inspect.knowledge.handlers")
