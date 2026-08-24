from __future__ import annotations

import hmac
import shutil
import tarfile
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath

from fire_inspect.operations.manifest import BackupManifest, verify_manifest

_BUNDLE_FILES = ("database.dump", "files.tar.gz", "manifest.json")


class RestoreValidationError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def _load_checksums(source: Path) -> dict[str, str]:
    checksum_file = source / "SHA256SUMS"
    if not checksum_file.is_file() or checksum_file.is_symlink():
        raise RestoreValidationError("备份校验文件缺失或类型无效")
    try:
        lines = checksum_file.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RestoreValidationError("备份校验文件不可读") from exc
    checksums: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] not in {"  ", " *"}:
            raise RestoreValidationError("备份校验文件格式无效")
        digest, filename = line[:64], line[66:]
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or filename not in _BUNDLE_FILES
            or filename in checksums
        ):
            raise RestoreValidationError("备份校验文件格式无效")
        checksums[filename] = digest
    if set(checksums) != set(_BUNDLE_FILES):
        raise RestoreValidationError("备份校验文件必须且只能覆盖三个备份工件")
    return checksums


def _safe_member_path(name: str, roots: set[str]) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.parts
        or path.parts[0] not in roots
        or any(part == ".env" or part.startswith(".env.") for part in path.parts)
    ):
        raise RestoreValidationError("备份文件归档包含不安全路径")
    return path


def _validate_archive(archive: Path, manifest: BackupManifest) -> tuple[tarfile.TarInfo, ...]:
    expected = {entry.relative_path: entry for entry in manifest.entries}
    roots = set(manifest.roots)
    regular_paths: set[str] = set()
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = tuple(bundle.getmembers())
    except (OSError, tarfile.TarError) as exc:
        raise RestoreValidationError("备份文件归档损坏") from exc
    if len(members) > max(10_000, len(expected) * 4 + 100):
        raise RestoreValidationError("备份文件归档条目数量异常")
    for member in members:
        path = _safe_member_path(member.name.rstrip("/"), roots)
        relative = path.as_posix()
        if member.isdir():
            continue
        if not member.isreg():
            raise RestoreValidationError("备份文件归档不能包含链接或特殊文件")
        entry = expected.get(relative)
        if entry is None or relative in regular_paths or member.size != entry.byte_size:
            raise RestoreValidationError("备份文件归档与逐文件清单不一致")
        regular_paths.add(relative)
    if regular_paths != set(expected):
        raise RestoreValidationError("备份文件归档与逐文件清单不一致")
    return members


def verify_backup_bundle(source: Path) -> BackupManifest:
    try:
        source = source.resolve()
        if not source.is_dir():
            raise RestoreValidationError("备份来源目录不存在")
    except OSError as exc:
        raise RestoreValidationError("备份来源目录不可访问") from exc
    checksums = _load_checksums(source)
    try:
        for filename in _BUNDLE_FILES:
            artifact = source / filename
            if not artifact.is_file() or artifact.is_symlink():
                raise RestoreValidationError("备份工件缺失或类型无效")
            if not hmac.compare_digest(_digest(artifact), checksums[filename]):
                raise RestoreValidationError("备份工件 SHA-256 校验失败")
    except OSError as exc:
        raise RestoreValidationError("备份工件不可读") from exc
    try:
        manifest = BackupManifest.from_json((source / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, KeyError) as exc:
        raise RestoreValidationError("备份逐文件清单无效") from exc
    _validate_archive(source / "files.tar.gz", manifest)
    return manifest


def restore_file_archive(*, source: Path, destination: Path) -> BackupManifest:
    manifest = verify_backup_bundle(source)
    try:
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()):
            raise RestoreValidationError("恢复数据目录必须为空")
        staging = Path(tempfile.mkdtemp(prefix=".restore-staging-", dir=destination))
    except RestoreValidationError:
        raise
    except OSError as exc:
        raise RestoreValidationError("恢复数据目录不可访问") from exc
    try:
        archive = source.resolve() / "files.tar.gz"
        roots = set(manifest.roots)
        with tarfile.open(archive, mode="r:gz") as bundle:
            for member in bundle:
                relative = _safe_member_path(member.name.rstrip("/"), roots)
                target = staging.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isreg():
                    raise RestoreValidationError("备份文件归档不能包含链接或特殊文件")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source_stream = bundle.extractfile(member)
                if source_stream is None:
                    raise RestoreValidationError("备份文件归档损坏")
                with source_stream, target.open("xb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                target.chmod(0o600)
        failures = verify_manifest(staging, manifest)
        if failures:
            raise RestoreValidationError("恢复文件与逐文件清单不一致")
        for root in manifest.roots:
            restored_root = staging / root
            if restored_root.exists():
                restored_root.replace(destination / root)
    except RestoreValidationError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise RestoreValidationError("恢复文件归档失败") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return manifest
