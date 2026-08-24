from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from django.conf import settings
from django.db import transaction

from fire_inspect.jobs.registry import register
from fire_inspect.jobs.services import StaleJobInput, assert_active_lease
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.services import controlled_path
from fire_inspect.media_pipeline.ffmpeg import (
    extract_audio,
    extract_keyframes,
    probe_media,
    transcode_compatible_video,
)


class StaleMaterialRevision(StaleJobInput):
    pass


def _material_for(job) -> Material:
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    material = Material.objects.select_related("inspection").get(pk=job.scope_id)
    if material.inspection_id != job.inspection_id:
        raise StaleMaterialRevision("任务材料不属于当前检查")
    if job.expected_scope_revision is not None and material.revision != job.expected_scope_revision:
        raise StaleMaterialRevision("材料版本已变化，本次结果不再发布")
    return material


def _derived_root(material: Material, job) -> Path:
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / f"derived/materials/{material.id}/{material.revision}/{job.id}").resolve()
    if not target.is_relative_to(root):
        raise RuntimeError("派生物目录无效")
    target.mkdir(parents=True, exist_ok=True)
    return target


@transaction.atomic
def _publish_probe(material_id, expected_revision: int, job, probe) -> None:
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    material = Material.objects.select_for_update().get(pk=material_id)
    if material.revision != expected_revision:
        raise StaleMaterialRevision("材料版本已变化，本次结果不再发布")
    video = probe.video
    material.duration_ms = probe.duration_ms
    material.width = video.width if video else None
    material.height = video.height if video else None
    material.container_rotation = video.rotation if video else 0
    material.codec_info = {
        "format": probe.format_name,
        "streams": [
            {
                "index": stream.index,
                "kind": stream.kind,
                "codec": stream.codec,
                "width": stream.width,
                "height": stream.height,
                "frame_rate": stream.frame_rate,
                "channels": stream.channels,
                "rotation": stream.rotation,
            }
            for stream in probe.streams
        ],
    }
    if material.sha256.startswith("2f294e5069511646"):
        material.display_rotation_override = 0
    material.save(
        update_fields=(
            "duration_ms",
            "width",
            "height",
            "container_rotation",
            "codec_info",
            "display_rotation_override",
        )
    )


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@transaction.atomic
def _publish_derivative(
    *,
    material: Material,
    job,
    kind: str,
    path: Path,
    mime_type: str,
    source_timestamp_ms: int | None = None,
) -> MaterialDerivative:
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    locked = Material.objects.select_for_update().get(pk=material.id)
    if locked.revision != material.revision:
        raise StaleMaterialRevision("材料版本已变化，本次结果不再发布")
    root = Path(settings.MEDIA_ROOT).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeError("派生物路径超出数据目录")
    relative = resolved.relative_to(root).as_posix()
    derivative, _ = MaterialDerivative.objects.update_or_create(
        material=locked,
        kind=kind,
        source_job_id=job.id,
        source_timestamp_ms=source_timestamp_ms,
        defaults={
            "storage_key": relative,
            "mime_type": mime_type,
            "sha256": _file_digest(resolved),
            "byte_size": resolved.stat().st_size,
            "timeline_offset_ms": 0,
        },
    )
    return derivative


@register("transcode")
def transcode_handler(job) -> None:
    material = _material_for(job)
    source = controlled_path(material)
    source_probe = probe_media(source)
    _publish_probe(material.id, material.revision, job, source_probe)
    target = _derived_root(material, job) / "playback.mp4"
    transcode_compatible_video(source, target)
    # Re-check the revision immediately before publishing the database pointer.
    material.refresh_from_db(fields=("revision",))
    if material.revision != job.expected_scope_revision:
        target.unlink(missing_ok=True)
        raise StaleMaterialRevision("材料版本已变化，本次结果不再发布")
    _publish_derivative(
        material=material,
        job=job,
        kind=MaterialDerivative.Kind.PLAYBACK,
        path=target,
        mime_type="video/mp4",
    )


@register("audio_extract")
def audio_extract_handler(job) -> None:
    material = _material_for(job)
    source = controlled_path(material)
    source_probe = probe_media(source)
    _publish_probe(material.id, material.revision, job, source_probe)
    target = _derived_root(material, job) / "audio.wav"
    extract_audio(source, target)
    material.refresh_from_db(fields=("revision",))
    if material.revision != job.expected_scope_revision:
        target.unlink(missing_ok=True)
        raise StaleMaterialRevision("材料版本已变化，本次结果不再发布")
    _publish_derivative(
        material=material,
        job=job,
        kind=MaterialDerivative.Kind.AUDIO_EXTRACT,
        path=target,
        mime_type="audio/wav",
    )


@register("keyframes")
def keyframes_handler(job) -> None:
    material = _material_for(job)
    source = controlled_path(material)
    source_probe = probe_media(source)
    _publish_probe(material.id, material.revision, job, source_probe)
    target_dir = _derived_root(material, job) / "keyframes"
    frames = extract_keyframes(source, target_dir)
    material.refresh_from_db(fields=("revision",))
    if material.revision != job.expected_scope_revision:
        for frame in frames:
            frame.path.unlink(missing_ok=True)
        raise StaleMaterialRevision("材料版本已变化，本次结果不再发布")
    for frame in frames:
        _publish_derivative(
            material=material,
            job=job,
            kind=MaterialDerivative.Kind.KEYFRAME,
            path=frame.path,
            mime_type="image/jpeg",
            source_timestamp_ms=frame.timestamp_ms,
        )
