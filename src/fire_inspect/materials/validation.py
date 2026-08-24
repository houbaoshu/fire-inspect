from __future__ import annotations

import hashlib
import shutil
import subprocess
import unicodedata
import uuid
import wave
from pathlib import Path, PurePosixPath

from django.conf import settings
from PIL import Image, UnidentifiedImageError


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


FILE_TYPES = {
    ".mp4": ("video", "video/mp4"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".mp3": ("audio", "audio/mpeg"),
    ".wav": ("audio", "audio/wav"),
    ".txt": ("text", "text/plain"),
}

MIME_ALIASES = {
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "image/jpg": "image/jpeg",
    "text/plain; charset=utf-8": "text/plain",
}

DANGEROUS_INNER_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".jar",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
    ".vbs",
}


def sanitize_display_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(name or "")).replace("\x00", "")
    normalized = normalized.replace("\\", "/")
    basename = PurePosixPath(normalized).name.strip().strip(".")
    basename = "".join(character for character in basename if character.isprintable())
    if not basename or basename in {".", ".."}:
        raise UploadValidationError("invalid_filename", "文件名无效。")
    return basename[:255]


def _sniff(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WAVE":
        return ".wav"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return ".mp4"
    if header.startswith(b"ID3") or (
        len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
    ):
        return ".mp3"
    return None


def _validate_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UploadValidationError("corrupt_file", "图片损坏或无法解码。") from exc


def _validate_wav(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as stream:
            if stream.getnchannels() < 1 or stream.getframerate() < 1:
                raise wave.Error("invalid audio metadata")
            duration_ms = round(stream.getnframes() * 1000 / stream.getframerate())
            if duration_ms <= 0:
                raise wave.Error("invalid audio duration")
            return duration_ms
    except (wave.Error, EOFError) as exc:
        raise UploadValidationError("corrupt_file", "录音损坏或无法解码。") from exc


def _validate_with_ffprobe(path: Path) -> int | None:
    executable = shutil.which("ffprobe")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            raise UploadValidationError("corrupt_file", "媒体文件损坏或无法解码。")
        try:
            duration_ms = round(float(result.stdout.decode("ascii").strip()) * 1000)
        except (UnicodeDecodeError, ValueError) as exc:
            raise UploadValidationError("corrupt_file", "媒体文件缺少可用的时长信息。") from exc
        if duration_ms <= 0:
            raise UploadValidationError("corrupt_file", "媒体文件缺少可用的时长信息。")
        return duration_ms
    except subprocess.TimeoutExpired as exc:
        raise UploadValidationError("media_probe_timeout", "媒体校验超时。") from exc


def _name_and_type(upload) -> tuple[str, str, str, str]:
    display_name = sanitize_display_name(upload.name)
    suffixes = PurePosixPath(display_name).suffixes
    extension = suffixes[-1].casefold() if suffixes else ""
    if extension not in FILE_TYPES:
        raise UploadValidationError("unsupported_file_type", "不支持此文件类型。")
    if any(suffix.casefold() in DANGEROUS_INNER_SUFFIXES for suffix in suffixes[:-1]):
        raise UploadValidationError("invalid_filename", "文件名包含不安全的双扩展名。")
    kind, mime_type = FILE_TYPES[extension]
    claimed_size = int(getattr(upload, "size", 0))
    if claimed_size <= 0:
        raise UploadValidationError("empty_file", "文件为空。")
    if claimed_size > settings.FIRE_INSPECT_MAX_UPLOAD_BYTES:
        raise UploadValidationError("file_too_large", "文件超过允许的大小上限。")
    declared = str(getattr(upload, "content_type", "") or "").casefold()
    declared = MIME_ALIASES.get(declared, declared)
    if declared and declared not in {mime_type, "application/octet-stream"}:
        raise UploadValidationError("file_type_mismatch", "文件扩展名与声明类型不一致。")
    return display_name, extension, kind, mime_type


def _validate_staged_content(
    *, path: Path, extension: str, kind: str
) -> tuple[int | None, int | None, int | None]:
    with path.open("rb") as stream:
        sniffed = _sniff(stream.read(64))
    if extension != ".txt":
        if sniffed is None:
            raise UploadValidationError("corrupt_file", "文件损坏或无法识别。")
        equivalent_jpeg = {extension, sniffed} <= {".jpg", ".jpeg"}
        if sniffed != extension and not equivalent_jpeg:
            raise UploadValidationError("file_type_mismatch", "文件扩展名与实际内容不一致。")

    width = height = duration_ms = None
    if kind == "image":
        width, height = _validate_image(path)
    elif extension == ".wav":
        duration_ms = _validate_wav(path)
    elif extension in {".mp4", ".mp3"}:
        duration_ms = _validate_with_ffprobe(path)
    else:
        try:
            with path.open("r", encoding="utf-8") as stream:
                while stream.read(64 * 1024):
                    pass
        except UnicodeDecodeError as exc:
            raise UploadValidationError(
                "invalid_text_encoding", "文字材料必须使用 UTF-8。"
            ) from exc
    if duration_ms is not None and duration_ms > settings.FIRE_INSPECT_MAX_MEDIA_DURATION_MS:
        raise UploadValidationError("media_too_long", "媒体时长超过允许的上限。")
    return width, height, duration_ms


def stage_and_validate_upload(upload) -> tuple[dict[str, object], Path]:
    display_name, extension, kind, mime_type = _name_and_type(upload)
    staging_root = Path(settings.FIRE_INSPECT_STAGING_ROOT)
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_path = staging_root / str(uuid.uuid4())
    digest = hashlib.sha256()
    byte_size = 0
    try:
        upload.seek(0)
        with staging_path.open("xb") as target:
            for chunk in upload.chunks(1024 * 1024):
                byte_size += len(chunk)
                if byte_size > settings.FIRE_INSPECT_MAX_UPLOAD_BYTES:
                    raise UploadValidationError("file_too_large", "文件超过允许的大小上限。")
                digest.update(chunk)
                target.write(chunk)
        upload.seek(0)
        if byte_size <= 0:
            raise UploadValidationError("empty_file", "文件为空。")
        width, height, duration_ms = _validate_staged_content(
            path=staging_path,
            extension=extension,
            kind=kind,
        )
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise

    return (
        {
            "display_name": display_name,
            "extension": extension,
            "kind": kind,
            "mime_type": mime_type,
            "sha256": digest.hexdigest(),
            "byte_size": byte_size,
            "width": width,
            "height": height,
            "duration_ms": duration_ms,
        },
        staging_path,
    )


def validate_upload(upload) -> dict[str, object]:
    """Validate without publishing; useful to callers that only need metadata."""
    metadata, staging_path = stage_and_validate_upload(upload)
    staging_path.unlink(missing_ok=True)
    return metadata
