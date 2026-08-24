from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum

from fire_inspect.audit.models import AuditEvent
from fire_inspect.materials.models import Material
from fire_inspect.materials.signals import material_deleted
from fire_inspect.materials.validation import (
    UploadValidationError,
    sanitize_display_name,
    stage_and_validate_upload,
)


def _enforce_inspection_limits(inspection, incoming_bytes: int) -> None:
    aggregate = Material.objects.filter(inspection=inspection).aggregate(
        count=Count("id"), total=Sum("byte_size")
    )
    if aggregate["count"] >= settings.FIRE_INSPECT_MAX_MATERIALS_PER_INSPECTION:
        raise UploadValidationError("material_count_limit", "本次检查的材料数量已达上限。")
    if (aggregate["total"] or 0) + incoming_bytes > settings.FIRE_INSPECT_MAX_INSPECTION_BYTES:
        raise UploadValidationError("inspection_size_limit", "本次检查的材料总量已达上限。")


def _publish_staged_material(material: Material, staging_path: Path) -> None:
    relative = Path(f"originals/inspections/{material.inspection_id}/{material.id}/original")
    root = Path(settings.MEDIA_ROOT).resolve()
    destination = (root / relative).resolve()
    if root not in destination.parents:
        raise UploadValidationError("invalid_storage_path", "文件存储位置无效。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging_path, destination)
    except OSError as exc:
        raise UploadValidationError("storage_publish_failed", "文件保存失败，请重试。") from exc
    material.original_file.name = relative.as_posix()


def _audit_material_created(material: Material, actor) -> None:
    AuditEvent.append(
        actor=actor,
        object_type="material",
        object_id=material.id,
        inspection_id_snapshot=material.inspection_id,
        action="created",
        masked_change_summary=f"material created: kind={material.kind}, bytes={material.byte_size}",
    )


@transaction.atomic
def create_uploaded_material(*, inspection, actor, upload) -> tuple[Material, bool]:
    metadata, staging_path = stage_and_validate_upload(upload)
    try:
        locked_inspection = type(inspection).objects.select_for_update().get(pk=inspection.pk)
        existing = Material.objects.filter(
            inspection=locked_inspection, sha256=metadata["sha256"]
        ).first()
        if existing:
            return existing, False
        _enforce_inspection_limits(locked_inspection, int(metadata["byte_size"]))
        material = Material(
            inspection=locked_inspection,
            created_by=actor,
            **metadata,
        )
        _publish_staged_material(material, staging_path)
        try:
            material.save(force_insert=True)
            _audit_material_created(material, actor)
        except Exception:
            Path(material.original_file.path).unlink(missing_ok=True)
            raise
        return material, True
    finally:
        staging_path.unlink(missing_ok=True)


@transaction.atomic
def create_text_material(
    *, inspection, actor, display_name: str, text: str
) -> tuple[Material, bool]:
    display_name = sanitize_display_name(display_name or "文字说明.txt")
    if not display_name.casefold().endswith(".txt"):
        display_name += ".txt"
    raw = text.encode("utf-8")
    if not raw:
        raise UploadValidationError("empty_text", "文字说明不能为空。")
    if len(raw) > settings.FIRE_INSPECT_MAX_UPLOAD_BYTES:
        raise UploadValidationError("file_too_large", "文字说明超过允许的大小上限。")
    digest = hashlib.sha256(raw).hexdigest()
    locked_inspection = type(inspection).objects.select_for_update().get(pk=inspection.pk)
    existing = Material.objects.filter(inspection=locked_inspection, sha256=digest).first()
    if existing:
        return existing, False
    _enforce_inspection_limits(locked_inspection, len(raw))
    material = Material(
        inspection=locked_inspection,
        created_by=actor,
        kind=Material.Kind.TEXT,
        display_name=display_name,
        mime_type="text/plain",
        extension=".txt",
        sha256=digest,
        byte_size=len(raw),
    )
    staging_root = Path(settings.FIRE_INSPECT_STAGING_ROOT)
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_path = staging_root / str(uuid.uuid4())
    try:
        staging_path.write_bytes(raw)
        _publish_staged_material(material, staging_path)
        try:
            material.save(force_insert=True)
            _audit_material_created(material, actor)
        except Exception:
            Path(material.original_file.path).unlink(missing_ok=True)
            raise
        return material, True
    finally:
        staging_path.unlink(missing_ok=True)


def delete_material(*, material: Material, actor, reason: str) -> None:
    quarantined: list[QuarantinedFile] = []
    try:
        with transaction.atomic():
            material = (
                Material.objects.select_for_update()
                .select_related("inspection")
                .get(pk=material.pk)
            )
            quarantined = quarantine_material_files([material])
            material_deleted.send(
                sender=Material,
                material=material,
                actor=actor,
                reason=reason,
                reason_code="SOURCE_DELETED",
            )
            AuditEvent.append(
                actor=actor,
                object_type="material",
                object_id=material.id,
                inspection_id_snapshot=material.inspection_id,
                action="deleted",
                reason=reason,
                masked_change_summary="material deleted",
            )
            material.delete()
    except Exception:
        restore_quarantined_files(quarantined)
        raise
    cleanup_quarantined_files(quarantined)


@dataclass(frozen=True, slots=True)
class QuarantinedFile:
    original: Path
    quarantined: Path


def quarantine_material_files(materials) -> list[QuarantinedFile]:
    operation_root = Path(settings.FIRE_INSPECT_QUARANTINE_ROOT) / str(uuid.uuid4())
    moved: list[QuarantinedFile] = []
    seen: set[Path] = set()
    try:
        for sequence, material in enumerate(materials):
            candidates = [("original", controlled_path(material))]
            candidates.extend(
                (f"derivative-{index}", controlled_storage_key(item.storage_key))
                for index, item in enumerate(material.derivatives.all())
            )
            for label, original in candidates:
                if original in seen or not original.is_file():
                    continue
                seen.add(original)
                quarantined = operation_root / str(sequence) / label
                quarantined.parent.mkdir(parents=True, exist_ok=True)
                os.replace(original, quarantined)
                moved.append(QuarantinedFile(original=original, quarantined=quarantined))
    except Exception:
        restore_quarantined_files(moved)
        raise
    return moved


def restore_quarantined_files(files: list[QuarantinedFile]) -> None:
    for item in reversed(files):
        if not item.quarantined.exists():
            continue
        item.original.parent.mkdir(parents=True, exist_ok=True)
        os.replace(item.quarantined, item.original)
    _remove_quarantine_roots(files)


def cleanup_quarantined_files(files: list[QuarantinedFile]) -> None:
    for item in files:
        item.quarantined.unlink(missing_ok=True)
    _remove_quarantine_roots(files)
    for item in files:
        _prune_empty_parents(item.original.parent, Path(settings.MEDIA_ROOT).resolve())


def _remove_quarantine_roots(files: list[QuarantinedFile]) -> None:
    roots = {item.quarantined.parents[1] for item in files if len(item.quarantined.parents) >= 2}
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)


def controlled_path(material: Material) -> Path:
    root = Path(settings.MEDIA_ROOT).resolve()
    path = Path(material.original_file.path).resolve()
    if path != root and root not in path.parents:
        raise UploadValidationError("invalid_storage_path", "文件存储位置无效。")
    return path


def controlled_storage_key(storage_key: str) -> Path:
    root = Path(settings.MEDIA_ROOT).resolve()
    key = Path(str(storage_key))
    if key.is_absolute():
        raise UploadValidationError("invalid_storage_path", "文件存储位置无效。")
    path = (root / key).resolve()
    if path == root or root not in path.parents:
        raise UploadValidationError("invalid_storage_path", "文件存储位置无效。")
    return path


def _prune_empty_parents(path: Path, stop: Path) -> None:
    current = path.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
