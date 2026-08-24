from django.apps import AppConfig


class FactsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.facts"
    label = "facts"

    def ready(self) -> None:
        from fire_inspect.facts import receivers  # noqa: F401
