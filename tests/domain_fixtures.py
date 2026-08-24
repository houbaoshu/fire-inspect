from __future__ import annotations

import hashlib

from django.core.files.base import ContentFile

from fire_inspect.materials.models import Material


def create_media_material(
    *, inspection, created_by, duration_ms: int = 10_000, name: str = "fixture.mp4"
) -> Material:
    raw = f"fixture:{inspection.id}:{name}".encode()
    material = Material(
        inspection=inspection,
        created_by=created_by,
        kind=Material.Kind.VIDEO,
        display_name=name,
        mime_type="video/mp4",
        extension=".mp4",
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
        duration_ms=duration_ms,
    )
    material.original_file.save("original", ContentFile(raw), save=False)
    material.save(force_insert=True)
    return material
