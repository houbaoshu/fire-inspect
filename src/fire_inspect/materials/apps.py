from django.apps import AppConfig


class MaterialsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fire_inspect.materials"
    label = "materials"

    def ready(self) -> None:
        from fire_inspect.materials import receivers  # noqa: F401
