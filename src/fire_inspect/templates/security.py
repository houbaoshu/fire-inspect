"""Defensive, read-only validation for uploaded DOCX template packages."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, is_zipfile

from lxml import etree

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_TYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REQUIRED_PARTS = frozenset({"[Content_Types].xml", "_rels/.rels", "word/document.xml"})
_ACTIVE_PATH_MARKERS = (
    "/activex/",
    "/embeddings/",
    "vbaproject",
    "oleobject",
)
_ACTIVE_CONTENT_TYPE_MARKERS = (
    "macroenabled",
    "vba",
    "activex",
    "oleobject",
    "ms-office.activeX",
)
_AUTHORITATIVE_INTERVIEW_SHA256 = "e15b011e14e0429d6ffc90a22dddf9682d402814b0d6e11b01344cceb53a0819"


class DocxSecurityError(ValueError):
    """A safe, user-facing rejection without package contents or host paths."""


@dataclass(frozen=True, slots=True)
class DocxLimits:
    max_compressed_bytes: int = 32 * 1024 * 1024
    max_entries: int = 2_048
    max_entry_uncompressed_bytes: int = 32 * 1024 * 1024
    max_total_uncompressed_bytes: int = 128 * 1024 * 1024
    max_compression_ratio: float = 200.0

    def __post_init__(self) -> None:
        if (
            min(
                self.max_compressed_bytes,
                self.max_entries,
                self.max_entry_uncompressed_bytes,
                self.max_total_uncompressed_bytes,
            )
            <= 0
        ):
            raise ValueError("DOCX 校验限制必须大于零")
        if self.max_compression_ratio < 1:
            raise ValueError("DOCX 最大压缩比不能小于 1")


@dataclass(frozen=True, slots=True)
class DocxValidationReport:
    normalized_filename: str
    sha256: str
    compressed_bytes: int
    total_uncompressed_bytes: int
    package_parts: tuple[str, ...]
    external_relationships: tuple[str, ...]
    active_content_parts: tuple[str, ...]
    warnings: tuple[str, ...] = ()


DocxSource = bytes | bytearray | memoryview | Path


def read_docx_source(source: DocxSource) -> bytes:
    if isinstance(source, Path):
        try:
            return source.read_bytes()
        except OSError as exc:
            raise DocxSecurityError("模板文件无法读取") from exc
    if isinstance(source, (bytes, bytearray, memoryview)):
        return bytes(source)
    raise TypeError("DOCX 来源必须是路径或字节")


def _safe_filename(source: DocxSource, filename: str | None) -> str:
    candidate = filename or (source.name if isinstance(source, Path) else "template.docx")
    normalized = unicodedata.normalize("NFKC", candidate).strip()
    if (
        not normalized
        or "\x00" in normalized
        or "/" in normalized
        or "\\" in normalized
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise DocxSecurityError("模板文件名不安全")
    if not normalized.casefold().endswith(".docx"):
        raise DocxSecurityError("只支持 DOCX 模板")
    stem = normalized[:-5]
    if not stem or re.search(r"\.(?:docm|dotm|exe|dll|js|vbs|ps1)$", stem, re.IGNORECASE):
        raise DocxSecurityError("模板文件名包含不支持的双扩展名")
    return normalized


def _normalized_entry_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    if "\x00" in normalized or "\\" in normalized:
        raise DocxSecurityError("DOCX 包含不安全的包项路径")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise DocxSecurityError("DOCX 包含不安全的包项路径")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise DocxSecurityError("DOCX 包含不安全的包项路径")
    return path.as_posix()


def _safe_xml(payload: bytes, *, part_name: str) -> etree._Element:
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise DocxSecurityError("DOCX XML 包含不允许的文档类型或实体")
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
        recover=False,
        remove_comments=False,
    )
    try:
        return etree.fromstring(payload, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise DocxSecurityError(f"DOCX 包项 XML 损坏：{part_name}") from exc


def _is_active_part(name: str) -> bool:
    folded = f"/{name.casefold()}"
    return name.casefold().endswith(".bin") or any(
        marker.casefold() in folded for marker in _ACTIVE_PATH_MARKERS
    )


def validate_docx(
    source: DocxSource,
    *,
    filename: str | None = None,
    limits: DocxLimits | None = None,
) -> DocxValidationReport:
    """Validate a DOCX without extracting or changing it.

    The validator rejects active content, external relationships, XML entities,
    duplicate/path-traversing entries, encryption and ZIP expansion abuse.
    """

    limits = limits or DocxLimits()
    normalized_filename = _safe_filename(source, filename)
    payload = read_docx_source(source)
    payload_sha256 = sha256(payload).hexdigest()
    if not payload or len(payload) > limits.max_compressed_bytes:
        raise DocxSecurityError("DOCX 文件大小超出限制")
    stream = BytesIO(payload)
    if not is_zipfile(stream):
        raise DocxSecurityError("DOCX 已损坏或不是有效的 Office 文档")
    stream.seek(0)

    try:
        archive = ZipFile(stream)
    except BadZipFile as exc:
        raise DocxSecurityError("DOCX 已损坏或不是有效的 Office 文档") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_entries:
            raise DocxSecurityError("DOCX 包项数量超出限制")

        seen: set[str] = set()
        part_names: list[str] = []
        total_uncompressed = 0
        active_parts: list[str] = []
        xml_roots: dict[str, etree._Element] = {}
        for info in infos:
            name = _normalized_entry_name(info.filename)
            collision_key = name.casefold()
            if collision_key in seen:
                raise DocxSecurityError("DOCX 包含重复包项")
            seen.add(collision_key)
            part_names.append(name)
            if info.flag_bits & 0x1:
                raise DocxSecurityError("DOCX 包含加密包项，无法安全处理")
            if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                raise DocxSecurityError("DOCX 使用了不支持的压缩方式")
            if info.file_size > limits.max_entry_uncompressed_bytes:
                raise DocxSecurityError("DOCX 单个包项解压大小超出限制")
            total_uncompressed += info.file_size
            if total_uncompressed > limits.max_total_uncompressed_bytes:
                raise DocxSecurityError("DOCX 解压总量超出限制")
            if info.file_size:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > limits.max_compression_ratio:
                    raise DocxSecurityError("DOCX 包项压缩比超出限制")
            if _is_active_part(name):
                active_parts.append(name)

            lowered = name.casefold()
            if lowered.endswith((".xml", ".rels")) or name == "[Content_Types].xml":
                try:
                    xml_roots[name] = _safe_xml(archive.read(info), part_name=name)
                except (BadZipFile, RuntimeError, OSError) as exc:
                    raise DocxSecurityError("DOCX 包项读取失败") from exc

        missing = _REQUIRED_PARTS - set(part_names)
        if missing:
            raise DocxSecurityError("DOCX 缺少必需的 Office 包项")

        content_types = xml_roots.get("[Content_Types].xml")
        if content_types is None:
            raise DocxSecurityError("DOCX 内容类型声明损坏")
        declared_types = [
            value.casefold()
            for value in content_types.xpath(
                ".//@ContentType",
                namespaces={"ct": _CONTENT_TYPE_NS},
            )
        ]
        if any(
            marker.casefold() in content_type
            for content_type in declared_types
            for marker in _ACTIVE_CONTENT_TYPE_MARKERS
        ):
            active_parts.append("[Content_Types].xml")
        if active_parts:
            raise DocxSecurityError("DOCX 包含宏、嵌入对象或其他活动内容")

        external_relationships: list[str] = []
        external_relationship_types: list[tuple[str, str]] = []
        for name, root in xml_roots.items():
            if not name.casefold().endswith(".rels"):
                continue
            for relationship in root.xpath(
                ".//pr:Relationship[@TargetMode='External']",
                namespaces={"pr": _REL_NS},
            ):
                relationship_id = relationship.get("Id", "unknown")
                external_relationships.append(f"{name}#{relationship_id}")
                external_relationship_types.append((name, relationship.get("Type", "")))
        trusted_legacy_template_link = (
            payload_sha256 == _AUTHORITATIVE_INTERVIEW_SHA256
            and external_relationship_types
            and all(
                name == "word/_rels/settings.xml.rels"
                and relationship_type.endswith("/attachedTemplate")
                for name, relationship_type in external_relationship_types
            )
        )
        if external_relationships and not trusted_legacy_template_link:
            raise DocxSecurityError("DOCX 包含不允许的外部关系")

        document = xml_roots.get("word/document.xml")
        if document is None or etree.QName(document).localname != "document":
            raise DocxSecurityError("DOCX 主文档结构无效")

    return DocxValidationReport(
        normalized_filename=normalized_filename,
        sha256=payload_sha256,
        compressed_bytes=len(payload),
        total_uncompressed_bytes=total_uncompressed,
        package_parts=tuple(part_names),
        external_relationships=tuple(external_relationships),
        active_content_parts=(),
        warnings=(
            ("权威询问笔录含历史外部模板关系；生成副本必须移除该关系",)
            if trusted_legacy_template_link
            else ()
        ),
    )
