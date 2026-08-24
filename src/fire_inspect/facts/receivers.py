from django.dispatch import receiver

from fire_inspect.facts.services import remove_deleted_material_evidence
from fire_inspect.materials.signals import material_deleted


@receiver(material_deleted)
def on_material_deleted(sender, material, actor, **kwargs) -> None:
    remove_deleted_material_evidence(material=material, actor=actor)
