from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from fire_inspect.materials.models import Material


@receiver(post_delete, sender=Material)
def delete_material_file(sender, instance: Material, **kwargs) -> None:
    if instance.original_file:
        storage = instance.original_file.storage
        name = instance.original_file.name
        transaction.on_commit(lambda: storage.delete(name))
