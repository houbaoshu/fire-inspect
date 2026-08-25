from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status

from fire_inspect.audit.models import AuditEvent
from fire_inspect.materials.middleware import UploadCapacityExhausted, acquire_upload_slot
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.services import delete_material
from fire_inspect.materials.signals import material_deleted
from fire_inspect.materials.validation import UploadValidationError, sanitize_display_name
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def png_upload(name: str = "现场.png", color: str = "red") -> SimpleUploadedFile:
    stream = BytesIO()
    Image.new("RGB", (16, 12), color=color).save(stream, format="PNG")
    return SimpleUploadedFile(name, stream.getvalue(), content_type="image/png")


def upload_url(inspection) -> str:
    return f"/api/v1/inspections/{inspection.id}/materials"


def test_image_upload_uses_generated_storage_path_and_deduplicates(
    authenticated_client, inspector_a, settings
) -> None:
    inspection = create_inspection(inspector_a)
    client = authenticated_client(inspector_a)

    first = client.post(upload_url(inspection), {"file": png_upload("../../现场.png")})
    assert first.status_code == status.HTTP_201_CREATED
    body = first.json()
    assert body["display_name"] == "现场.png"
    assert body["kind"] == "image"
    assert body["deduplicated"] is False
    material = Material.objects.get(id=body["id"])
    assert material.original_file.name == (
        f"originals/inspections/{inspection.id}/{material.id}/original"
    )
    assert "现场.png" not in material.original_file.name
    assert Path(material.original_file.path).is_file()

    duplicate = client.post(upload_url(inspection), {"file": png_upload("copy.png")})
    assert duplicate.status_code == status.HTTP_200_OK
    assert duplicate.json()["id"] == body["id"]
    assert duplicate.json()["deduplicated"] is True
    assert Material.objects.count() == 1


def test_text_note_upload(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    response = authenticated_client(inspector_a).post(
        upload_url(inspection),
        {"kind": "text", "display_name": "现场说明.txt", "text": "人工补充说明"},
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["kind"] == "text"


@pytest.mark.parametrize(
    ("upload", "expected_code"),
    [
        (
            SimpleUploadedFile("payload.exe", b"MZ...", content_type="application/octet-stream"),
            "unsupported_file_type",
        ),
        (
            SimpleUploadedFile("broken.png", b"not an image", content_type="image/png"),
            "corrupt_file",
        ),
        (
            SimpleUploadedFile("fake.jpg", b"\x89PNG\r\n\x1a\nnot-jpeg", content_type="image/jpeg"),
            "file_type_mismatch",
        ),
    ],
)
def test_unsupported_corrupt_or_mismatched_file_is_rejected(
    authenticated_client, inspector_a, upload, expected_code
) -> None:
    inspection = create_inspection(inspector_a)
    response = authenticated_client(inspector_a).post(upload_url(inspection), {"file": upload})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == expected_code
    assert Material.objects.count() == 0
    assert "/Users/" not in str(response.json())


def test_file_size_limit_is_enforced(authenticated_client, inspector_a, settings) -> None:
    settings.FIRE_INSPECT_MAX_UPLOAD_BYTES = 10
    inspection = create_inspection(inspector_a)
    response = authenticated_client(inspector_a).post(
        upload_url(inspection), {"file": png_upload()}
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "file_too_large"


def test_media_duration_limit_is_enforced(
    authenticated_client, inspector_a, settings, monkeypatch
) -> None:
    settings.FIRE_INSPECT_MAX_MEDIA_DURATION_MS = 1_000
    monkeypatch.setattr(
        "fire_inspect.materials.validation._validate_with_ffprobe",
        lambda path: 1_001,
    )
    inspection = create_inspection(inspector_a)
    upload = SimpleUploadedFile(
        "too-long.mp4",
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32,
        content_type="video/mp4",
    )

    response = authenticated_client(inspector_a).post(upload_url(inspection), {"file": upload})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["code"] == "media_too_long"
    assert Material.objects.count() == 0


def test_upload_slot_limit_is_cross_process_lock_backed(tmp_path: Path) -> None:
    first = acquire_upload_slot(root=tmp_path / "slots", capacity=1)
    try:
        with pytest.raises(UploadCapacityExhausted):
            acquire_upload_slot(root=tmp_path / "slots", capacity=1)
    finally:
        first.release()
    second = acquire_upload_slot(root=tmp_path / "slots", capacity=1)
    second.release()


def test_upload_concurrency_limit_rejects_before_material_creation(
    api_client, inspector_a, monkeypatch
) -> None:
    inspection = create_inspection(inspector_a)

    def refuse_slot(**kwargs):
        raise UploadCapacityExhausted

    monkeypatch.setattr("fire_inspect.materials.middleware.acquire_upload_slot", refuse_slot)
    api_client.force_login(inspector_a)
    response = api_client.post(upload_url(inspection), {"file": png_upload()})

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.json()["error"]["code"] == "upload_concurrency_limit"
    assert response["Retry-After"] == "1"
    assert Material.objects.count() == 0


def test_upload_limit_does_not_replace_unauthenticated_401(
    api_client, inspector_a, monkeypatch
) -> None:
    inspection = create_inspection(inspector_a)

    def refuse_slot(**kwargs):
        raise UploadCapacityExhausted

    monkeypatch.setattr("fire_inspect.materials.middleware.acquire_upload_slot", refuse_slot)
    response = api_client.post(upload_url(inspection), {"file": png_upload()})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_cross_user_cannot_list_download_or_guess_material(
    authenticated_client, inspector_a, inspector_b
) -> None:
    inspection = create_inspection(inspector_a)
    created = authenticated_client(inspector_a).post(upload_url(inspection), {"file": png_upload()})
    material_id = created.json()["id"]
    other = authenticated_client(inspector_b)
    assert other.get(upload_url(inspection)).status_code == status.HTTP_404_NOT_FOUND
    assert other.get(f"/api/v1/materials/{material_id}/download").status_code == 404
    assert other.get(f"/api/v1/materials/{material_id}/play").status_code == 404


def test_unauthenticated_download_is_401(api_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="content",
    )
    response = api_client.get(f"/api/v1/materials/{material.id}/download")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_download_and_range_playback(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="abcdef",
    )
    client = authenticated_client(inspector_a)
    download = client.get(f"/api/v1/materials/{material.id}/download")
    assert download.status_code == status.HTTP_200_OK
    assert b"".join(download.streaming_content) == b"abcdef"
    assert "note.txt" in download["Content-Disposition"]

    partial = client.get(f"/api/v1/materials/{material.id}/play", HTTP_RANGE="bytes=1-3")
    assert partial.status_code == status.HTTP_206_PARTIAL_CONTENT
    assert b"".join(partial.streaming_content) == b"bcd"
    assert partial["Content-Range"] == "bytes 1-3/6"


def test_image_and_derivative_previews_are_protected(
    authenticated_client, api_client, inspector_a, inspector_b, settings
) -> None:
    inspection = create_inspection(inspector_a)
    owner = authenticated_client(inspector_a)
    created = owner.post(upload_url(inspection), {"file": png_upload()})
    assert created.status_code == status.HTTP_201_CREATED
    material = Material.objects.get(pk=created.json()["id"])
    assert created.json()["thumbnail_url"] == (f"/api/v1/materials/{material.id}/preview/")

    storage_key = f"derived/materials/{material.id}/1/test/keyframe.jpg"
    derivative_path = Path(settings.MEDIA_ROOT) / storage_key
    derivative_path.parent.mkdir(parents=True, exist_ok=True)
    derivative_path.write_bytes(b"protected-keyframe")
    derivative = MaterialDerivative.objects.create(
        material=material,
        kind=MaterialDerivative.Kind.KEYFRAME,
        storage_key=storage_key,
        mime_type="image/jpeg",
        sha256="b" * 64,
        byte_size=18,
        source_timestamp_ms=1_000,
    )

    image_preview = owner.get(f"/api/v1/materials/{material.id}/preview/")
    assert image_preview.status_code == status.HTTP_200_OK
    assert image_preview["Content-Disposition"].startswith("inline;")
    assert b"".join(image_preview.streaming_content).startswith(b"\x89PNG")

    derivative_preview = owner.get(f"/api/v1/material-derivatives/{derivative.id}/preview/")
    assert derivative_preview.status_code == status.HTTP_200_OK
    assert derivative_preview["Content-Disposition"].startswith("inline;")
    assert b"".join(derivative_preview.streaming_content) == b"protected-keyframe"
    derivative_download = owner.get(f"/api/v1/material-derivatives/{derivative.id}/download/")
    assert derivative_download.status_code == status.HTTP_200_OK
    assert derivative_download["Content-Disposition"].startswith("attachment;")

    other = authenticated_client(inspector_b)
    assert other.get(f"/api/v1/materials/{material.id}/preview/").status_code == 404
    assert other.get(f"/api/v1/material-derivatives/{derivative.id}/preview/").status_code == 404
    api_client.force_authenticate(user=None)
    assert (
        api_client.get(f"/api/v1/material-derivatives/{derivative.id}/preview/").status_code
        == status.HTTP_401_UNAUTHORIZED
    )


def test_play_rejects_unreasonably_large_range_integer(authenticated_client, inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="range.txt",
        text="abcdef",
    )
    response = authenticated_client(inspector_a).get(
        f"/api/v1/materials/{material.id}/play",
        HTTP_RANGE=f"bytes={'9' * 5000}-",
    )

    assert response.status_code == status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE


def test_material_delete_requires_confirmation_and_removes_file(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="abcdef",
    )
    original_path = Path(material.original_file.path)
    client = authenticated_client(inspector_a)
    refused = client.delete(
        f"/api/v1/materials/{material.id}",
        {"confirm": False, "reason": "上传错误"},
        format="json",
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    deleted = client.delete(
        f"/api/v1/materials/{material.id}",
        {"confirm": True, "reason": "上传错误"},
        format="json",
    )
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    assert not original_path.exists()
    assert AuditEvent.objects.filter(
        object_type="material", object_id=material.id, action="deleted"
    ).exists()


def test_material_delete_restores_file_when_domain_receiver_fails(inspector_a) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="abcdef",
    )
    original_path = Path(material.original_file.path)

    def failing_receiver(sender, **kwargs):
        raise RuntimeError("receiver failure")

    material_deleted.connect(failing_receiver, weak=False)
    try:
        with pytest.raises(RuntimeError):
            delete_material(material=material, actor=inspector_a, reason="测试回滚")
    finally:
        material_deleted.disconnect(failing_receiver)
    assert Material.objects.filter(pk=material.pk).exists()
    assert original_path.is_file()


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("../../secret.png", "secret.png"),
        ("C:\\temp\\secret.png", "secret.png"),
        (" 控制室\x00.png ", "控制室.png"),
    ],
)
def test_display_filename_is_safely_normalized(name, expected) -> None:
    assert sanitize_display_name(name) == expected


def test_empty_filename_is_rejected() -> None:
    with pytest.raises(UploadValidationError):
        sanitize_display_name("../")
