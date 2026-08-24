from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from fire_inspect.templates import (
    DocxLimits,
    DocxSecurityError,
    validate_docx,
)

EXPECTED_HASHES = {
    "照片.docx": "6199a905ebd4a0453c90982f5a23987ea54e15ef0c1e8e004b1e50be3d13cdac",
    "询问笔录.docx": "e15b011e14e0429d6ffc90a22dddf9682d402814b0d6e11b01344cceb53a0819",
    "监督检查记录表.docx": "37726202a5ae32b96230504388611c9cc824b3cc1abb798b52efd091f72baae5",
}


def _package(*, extra: dict[str, bytes] | None = None) -> bytes:
    parts = {
        "[Content_Types].xml": (
            b'<?xml version="1.0"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            b"</Types>"
        ),
        "_rels/.rels": (
            b'<?xml version="1.0"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            b'Target="word/document.xml"/>'
            b"</Relationships>"
        ),
        "word/document.xml": (
            b'<?xml version="1.0"?>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b"<w:body><w:p/></w:body></w:document>"
        ),
    }
    parts.update(extra or {})
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize("filename,expected", EXPECTED_HASHES.items())
def test_authoritative_templates_are_valid_and_unchanged(
    template_dir: Path, filename: str, expected: str
) -> None:
    path = template_dir / filename
    before = path.read_bytes()
    report = validate_docx(path, filename=filename)
    after = path.read_bytes()

    assert report.sha256 == expected
    if filename == "询问笔录.docx":
        assert report.external_relationships == ("word/_rels/settings.xml.rels#rId1",)
        assert report.warnings
    else:
        assert report.external_relationships == ()
    assert report.active_content_parts == ()
    assert before == after
    assert sha256(after).hexdigest() == expected


def test_corrupt_package_has_a_safe_error() -> None:
    secret = b"NOT-A-ZIP SECRET-CONTENT-CANARY"
    with pytest.raises(DocxSecurityError) as error:
        validate_docx(secret, filename="template.docx")
    assert "SECRET-CONTENT-CANARY" not in str(error.value)


@pytest.mark.parametrize(
    "entry",
    ("../outside.xml", "/absolute.xml", "C:/windows.xml", r"..\\outside.xml"),
)
def test_package_entry_path_traversal_is_rejected(entry: str) -> None:
    with pytest.raises(DocxSecurityError, match="路径"):
        validate_docx(_package(extra={entry: b"unsafe"}), filename="template.docx")


def test_duplicate_package_entry_is_rejected() -> None:
    stream = BytesIO(_package())
    rebuilt = BytesIO()
    with ZipFile(stream) as source, ZipFile(rebuilt, "w", ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
        target.writestr("word/document.xml", b"<duplicate/>")

    with pytest.raises(DocxSecurityError, match="重复"):
        validate_docx(rebuilt.getvalue(), filename="template.docx")


def test_macro_or_embedded_active_content_is_rejected() -> None:
    for part in ("word/vbaProject.bin", "word/embeddings/oleObject1.bin"):
        with pytest.raises(DocxSecurityError, match="活动内容"):
            validate_docx(_package(extra={part: b"active"}), filename="template.docx")


def test_external_relationship_is_rejected() -> None:
    relationship = (
        b'<?xml version="1.0"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId9" Type="urn:test" Target="https://example.invalid/x" '
        b'TargetMode="External"/>'
        b"</Relationships>"
    )
    with pytest.raises(DocxSecurityError, match="外部关系"):
        validate_docx(
            _package(extra={"word/_rels/document.xml.rels": relationship}),
            filename="template.docx",
        )


def test_doctype_and_entities_are_rejected_before_xml_parse() -> None:
    document = b'<!DOCTYPE x [<!ENTITY boom "unsafe">]><x>&boom;</x>'
    with pytest.raises(DocxSecurityError, match="实体"):
        validate_docx(
            _package(extra={"word/document.xml": document}),
            filename="template.docx",
        )


def test_limits_reject_oversized_or_overexpanded_packages(template_dir: Path) -> None:
    with pytest.raises(DocxSecurityError, match="解压"):
        validate_docx(
            template_dir / "照片.docx",
            limits=DocxLimits(max_total_uncompressed_bytes=100),
        )
    with pytest.raises(DocxSecurityError, match="压缩比"):
        validate_docx(
            template_dir / "照片.docx",
            limits=DocxLimits(max_compression_ratio=1.01),
        )
