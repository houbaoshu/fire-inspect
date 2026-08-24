from __future__ import annotations

import zipfile
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath

from lxml import etree
from pypdf import PdfReader

from fire_inspect.materials.validation import UploadValidationError, sanitize_display_name

MAX_DOCUMENT_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500

MIME_TYPES = {
    ".pdf": {"application/pdf", "application/x-pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
}


def _read_upload(upload) -> bytes:
    upload.seek(0)
    payload = upload.read(MAX_DOCUMENT_BYTES + 1)
    upload.seek(0)
    if not payload:
        raise UploadValidationError("empty_file", "文件为空。")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise UploadValidationError("file_too_large", "知识文档超过允许的大小上限。")
    return payload


def _validate_docx(payload: bytes) -> None:
    try:
        archive = zipfile.ZipFile(BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise UploadValidationError("corrupt_file", "DOCX 已损坏或无法打开。") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise UploadValidationError("unsafe_docx", "DOCX 包含过多文件项。")
        total = 0
        names = set()
        for entry in entries:
            raw_name = entry.filename
            portable_name = raw_name.replace("\\", "/")
            normalized = PurePosixPath(portable_name)
            if (
                "\x00" in raw_name
                or normalized.is_absolute()
                or PureWindowsPath(portable_name).drive
                or ".." in normalized.parts
            ):
                raise UploadValidationError("unsafe_docx", "DOCX 包含不安全路径。")
            normalized_name = normalized.as_posix()
            lowered = normalized_name.casefold()
            if normalized_name in names:
                raise UploadValidationError("unsafe_docx", "DOCX 包含重复文件项。")
            if entry.flag_bits & 0x1:
                raise UploadValidationError("unsafe_docx", "DOCX 包含加密文件项。")
            if any(
                marker in lowered
                for marker in ("vbaproject", "/embeddings/", "/oleobject", "activex")
            ):
                raise UploadValidationError("unsafe_docx", "DOCX 包含不支持的活动或嵌入对象。")
            total += entry.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise UploadValidationError("unsafe_docx", "DOCX 解压后体积超过限制。")
            if entry.file_size and (
                not entry.compress_size
                or entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO
            ):
                raise UploadValidationError("unsafe_docx", "DOCX 压缩比异常。")
            names.add(normalized_name)
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise UploadValidationError("corrupt_file", "DOCX 缺少必要结构。")
        for name in names:
            if not name.casefold().endswith(".rels"):
                continue
            try:
                root = etree.fromstring(archive.read(name))
            except etree.XMLSyntaxError as exc:
                raise UploadValidationError("corrupt_file", "DOCX 关系结构损坏。") from exc
            if root.xpath("//*[local-name()='Relationship'][@TargetMode='External']"):
                raise UploadValidationError("unsafe_docx", "DOCX 包含外部关系。")


def _validate_pdf(payload: bytes) -> None:
    if not payload.startswith(b"%PDF-"):
        raise UploadValidationError("file_type_mismatch", "文件扩展名与实际内容不一致。")
    try:
        reader = PdfReader(BytesIO(payload))
        if reader.is_encrypted:
            raise UploadValidationError("unsupported_pdf", "暂不支持加密 PDF。")
        if not reader.pages:
            raise UploadValidationError("corrupt_file", "PDF 没有页面。")
        root = reader.trailer.get("/Root", {})
        if root.get("/OpenAction") or "/JavaScript" in str(root.get("/Names", {})):
            raise UploadValidationError("unsafe_pdf", "PDF 包含不支持的活动内容。")
    except UploadValidationError:
        raise
    except Exception as exc:
        raise UploadValidationError("corrupt_file", "PDF 已损坏或无法打开。") from exc


def validate_knowledge_upload(upload) -> dict[str, object]:
    display_name = sanitize_display_name(upload.name)
    extension = PurePosixPath(display_name).suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise UploadValidationError("unsupported_file_type", "知识库仅支持 PDF 和 DOCX。")
    declared = str(getattr(upload, "content_type", "") or "").casefold().split(";", 1)[0].strip()
    if declared and declared not in MIME_TYPES[extension]:
        raise UploadValidationError("file_type_mismatch", "文件扩展名与声明类型不一致。")
    payload = _read_upload(upload)
    if extension == ".pdf":
        _validate_pdf(payload)
        mime_type = "application/pdf"
    else:
        _validate_docx(payload)
        mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return {
        "display_name": display_name,
        "extension": extension,
        "mime_type": mime_type,
        "payload": payload,
        "sha256": sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }
