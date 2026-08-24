from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status

from fire_inspect.audit.models import AuditEvent
from fire_inspect.common.api import ApiError, api_exception_handler
from fire_inspect.knowledge.security import validate_knowledge_upload
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.validation import UploadValidationError
from tests.inspections.test_inspection_api import create_inspection

pytestmark = pytest.mark.django_db


def _png(name: str, *, content_type: str = "image/png") -> SimpleUploadedFile:
    payload = BytesIO()
    Image.new("RGB", (4, 4), color="red").save(payload, format="PNG")
    return SimpleUploadedFile(name, payload.getvalue(), content_type=content_type)


def _docx(entries: dict[str, bytes], *, content_type: str) -> SimpleUploadedFile:
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return SimpleUploadedFile("test.docx", payload.getvalue(), content_type=content_type)


def _minimal_docx_entries() -> dict[str, bytes]:
    return {
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "word/document.xml": b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
    }


@pytest.mark.parametrize(
    "name",
    (
        "../../outside.png",
        "..%2f..%2foutside.png",
        "C:\\sensitive\\outside.png",
        "\\\\server\\share\\outside.png",
        "／etc／outside.png",
        "outside\x00.png",
    ),
)
def test_malicious_upload_name_never_controls_storage_path(
    authenticated_client, inspector_a, settings, name
) -> None:
    inspection = create_inspection(inspector_a)
    response = authenticated_client(inspector_a).post(
        f"/api/v1/inspections/{inspection.id}/materials", {"file": _png(name)}
    )
    assert response.status_code == status.HTTP_201_CREATED
    material = Material.objects.get(pk=response.json()["id"])
    path = Path(material.original_file.path).resolve()
    assert path.is_relative_to(Path(settings.MEDIA_ROOT).resolve())
    assert path.name == "original"
    assert material.display_name.endswith("outside.png")


def test_dangerous_double_extension_and_declared_mime_mismatch_are_rejected(
    authenticated_client, inspector_a
) -> None:
    inspection = create_inspection(inspector_a)
    client = authenticated_client(inspector_a)
    double = client.post(
        f"/api/v1/inspections/{inspection.id}/materials",
        {"file": _png("payload.exe.png")},
    )
    mismatch = client.post(
        f"/api/v1/inspections/{inspection.id}/materials",
        {"file": _png("scene.png", content_type="text/html")},
    )
    assert double.status_code == status.HTTP_400_BAD_REQUEST
    assert double.json()["error"]["code"] == "invalid_filename"
    assert mismatch.status_code == status.HTTP_400_BAD_REQUEST
    assert mismatch.json()["error"]["code"] == "file_type_mismatch"
    assert Material.objects.count() == 0


def test_docx_declared_mime_must_match_validated_package() -> None:
    upload = _docx(_minimal_docx_entries(), content_type="image/png")
    with pytest.raises(UploadValidationError, match="声明类型") as caught:
        validate_knowledge_upload(upload)
    assert caught.value.code == "file_type_mismatch"


@pytest.mark.parametrize(
    "unsafe_name",
    ("../escape.xml", "/absolute.xml", "C:/escape.xml", "\\\\server\\share.xml"),
)
def test_docx_rejects_unsafe_zip_member_paths(unsafe_name: str) -> None:
    entries = _minimal_docx_entries()
    entries[unsafe_name] = b"unsafe"
    upload = _docx(
        entries,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    with pytest.raises(UploadValidationError) as caught:
        validate_knowledge_upload(upload)
    assert caught.value.code == "unsafe_docx"


def test_docx_rejects_external_relationships_and_active_objects() -> None:
    for name, payload in (
        (
            "word/_rels/document.xml.rels",
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="x" Target="https://example.invalid/payload" TargetMode="External"/></Relationships>',
        ),
        ("word/embeddings/payload.bin", b"executable"),
    ):
        entries = _minimal_docx_entries()
        entries[name] = payload
        upload = _docx(
            entries,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        with pytest.raises(UploadValidationError) as caught:
            validate_knowledge_upload(upload)
        assert caught.value.code == "unsafe_docx"


def test_material_delete_removes_original_and_all_derivative_files(
    authenticated_client, inspector_a, settings
) -> None:
    inspection = create_inspection(inspector_a)
    material = Material.objects.create_text(
        inspection=inspection,
        created_by=inspector_a,
        display_name="note.txt",
        text="original",
    )
    derivative_paths = []
    for sequence in range(2):
        storage_key = f"derived/materials/{material.id}/1/job-{sequence}/result.bin"
        path = Path(settings.MEDIA_ROOT) / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"derived-{sequence}".encode())
        derivative_paths.append(path)
        MaterialDerivative.objects.create(
            material=material,
            kind=MaterialDerivative.Kind.PREVIEW,
            storage_key=storage_key,
            mime_type="application/octet-stream",
            sha256=str(sequence).zfill(64),
            byte_size=path.stat().st_size,
        )
    original = Path(material.original_file.path)

    response = authenticated_client(inspector_a).delete(
        f"/api/v1/materials/{material.id}",
        {"confirm": True, "reason": "重复材料"},
        format="json",
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not original.exists()
    assert all(not path.exists() for path in derivative_paths)
    assert not MaterialDerivative.objects.filter(material_id=material.id).exists()


def test_audit_and_api_payloads_redact_canary_secrets_paths_and_identifiers(
    authenticated_client, inspector_a, admin_user, settings
) -> None:
    inspection = create_inspection(inspector_a)
    canary = (
        f"api_key=CANARY_AI_SECRET sessionid=CANARY_SESSION "
        f"phone=13812345678 id=330106199001011234 path={settings.MEDIA_ROOT}/private.bin"
    )
    deleted = authenticated_client(inspector_a).delete(
        f"/api/v1/inspections/{inspection.id}",
        {"confirm": True, "reason": canary},
        format="json",
    )
    assert deleted.status_code == status.HTTP_204_NO_CONTENT
    event = AuditEvent.objects.get(
        object_type="inspection", object_id=inspection.id, action="deleted"
    )
    serialized = str(authenticated_client(admin_user).get("/api/v1/admin/audit").json())
    for forbidden in (
        "CANARY_AI_SECRET",
        "CANARY_SESSION",
        "13812345678",
        "330106199001011234",
        str(settings.MEDIA_ROOT),
    ):
        assert forbidden not in event.reason
        assert forbidden not in serialized


def test_api_exception_boundary_never_echoes_canary_or_unhandled_exception() -> None:
    canary = "api_key=CANARY_AI_SECRET sessionid=CANARY_SESSION /Users/private/secret"
    handled = api_exception_handler(
        ApiError(code="provider_failed", message=canary), {"view": object()}
    )
    assert handled.status_code == 400
    assert "CANARY_AI_SECRET" not in str(handled.data)
    assert "CANARY_SESSION" not in str(handled.data)

    unhandled = api_exception_handler(RuntimeError(canary), {"view": object()})
    assert unhandled.status_code == 500
    assert unhandled.data == {
        "error": {
            "code": "internal_error",
            "message": "服务暂时无法处理请求，请稍后重试。",
        }
    }
