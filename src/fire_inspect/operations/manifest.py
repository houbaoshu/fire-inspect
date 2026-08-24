from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    relative_path: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    schema_version: int
    created_at: str
    application_version: str
    migration_revision: str
    roots: tuple[str, ...]
    entries: tuple[ManifestEntry, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, payload: str) -> BackupManifest:
        data = json.loads(payload)
        schema_version = data.get("schema_version")
        if schema_version not in {1, 2}:
            raise ValueError("备份清单版本不受支持")
        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("备份清单内容无效")
        entries: list[ManifestEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                raise ValueError("备份清单条目无效")
            try:
                entry = ManifestEntry(
                    relative_path=str(item["relative_path"]),
                    byte_size=int(item["byte_size"]),
                    sha256=str(item["sha256"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("备份清单条目无效") from exc
            _validate_manifest_entry(entry)
            entries.append(entry)
        if len({entry.relative_path for entry in entries}) != len(entries):
            raise ValueError("备份清单包含重复路径")
        if schema_version == 2:
            raw_roots = data.get("roots")
            if not isinstance(raw_roots, list) or not raw_roots:
                raise ValueError("备份清单根目录无效")
            roots = tuple(str(item) for item in raw_roots)
        else:
            roots = tuple(
                sorted({PurePosixPath(entry.relative_path).parts[0] for entry in entries})
            )
        for root in roots:
            _validate_relative_root(root)
        if len(set(roots)) != len(roots):
            raise ValueError("备份清单根目录重复")
        root_set = set(roots)
        if any(PurePosixPath(entry.relative_path).parts[0] not in root_set for entry in entries):
            raise ValueError("备份清单条目不属于声明的数据目录")
        return cls(
            schema_version=int(schema_version),
            created_at=str(data["created_at"]),
            application_version=str(data["application_version"]),
            migration_revision=str(data["migration_revision"]),
            roots=roots,
            entries=tuple(entries),
        )


def _validate_relative_root(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or len(path.parts) != 1 or path.parts[0] in {".", ".."}:
        raise ValueError("备份根目录无效")


def _validate_manifest_entry(entry: ManifestEntry) -> None:
    path = PurePosixPath(entry.relative_path)
    if (
        not entry.relative_path
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or entry.byte_size < 0
        or not _SHA256_RE.fullmatch(entry.sha256)
    ):
        raise ValueError("备份清单条目无效")


def _is_process_secret(path: Path) -> bool:
    return path.name == ".env" or path.name.startswith(".env.")


def _digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def build_manifest(
    root: Path,
    relative_roots: Iterable[str],
    *,
    application_version: str,
    migration_revision: str,
    created_at: datetime | None = None,
) -> BackupManifest:
    root = root.resolve()
    entries: list[ManifestEntry] = []
    roots = tuple(dict.fromkeys(str(item) for item in relative_roots))
    for relative_root in roots:
        _validate_relative_root(relative_root)
        unresolved_candidate = root / relative_root
        if unresolved_candidate.is_symlink():
            raise ValueError("备份目录不能包含符号链接")
        candidate = unresolved_candidate.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("备份目标超出数据目录")
        if not candidate.exists():
            continue
        paths = [candidate] if candidate.is_file() else sorted(candidate.rglob("*"))
        for path in paths:
            if path.is_symlink():
                raise ValueError("备份目录不能包含符号链接")
            if not path.is_file() or _is_process_secret(path):
                continue
            relative_path = path.relative_to(root).as_posix()
            entry = ManifestEntry(relative_path, path.stat().st_size, _digest(path))
            _validate_manifest_entry(entry)
            entries.append(entry)
    entries.sort(key=lambda item: item.relative_path)
    event_time = created_at or datetime.now(UTC)
    return BackupManifest(
        schema_version=2,
        created_at=event_time.isoformat(),
        application_version=application_version,
        migration_revision=migration_revision,
        roots=roots,
        entries=tuple(entries),
    )


def verify_manifest(root: Path, manifest: BackupManifest) -> tuple[str, ...]:
    root = root.resolve()
    failures: list[str] = []
    expected_paths = {entry.relative_path for entry in manifest.entries}
    for entry in manifest.entries:
        path = (root / entry.relative_path).resolve()
        if not path.is_relative_to(root):
            failures.append(f"{entry.relative_path}: path escapes restore root")
        elif not path.is_file():
            failures.append(f"{entry.relative_path}: missing")
        elif path.stat().st_size != entry.byte_size:
            failures.append(f"{entry.relative_path}: size mismatch")
        elif _digest(path) != entry.sha256:
            failures.append(f"{entry.relative_path}: hash mismatch")
    actual_paths: set[str] = set()
    for relative_root in manifest.roots:
        candidate = root / relative_root
        if not candidate.exists():
            continue
        for path in [candidate] if candidate.is_file() else sorted(candidate.rglob("*")):
            if path.is_symlink():
                failures.append(f"{path.relative_to(root).as_posix()}: symlink is forbidden")
            elif path.is_file() and not _is_process_secret(path):
                actual_paths.add(path.relative_to(root).as_posix())
    for unexpected in sorted(actual_paths - expected_paths):
        failures.append(f"{unexpected}: unexpected")
    return tuple(failures)
