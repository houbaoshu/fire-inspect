from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from fire_inspect.operations.manifest import build_manifest, verify_manifest
from fire_inspect.operations.restore import (
    RestoreValidationError,
    restore_file_archive,
    verify_backup_bundle,
)


def _write_checksums(source: Path) -> None:
    lines = []
    for filename in ("database.dump", "files.tar.gz", "manifest.json"):
        digest = sha256((source / filename).read_bytes()).hexdigest()
        lines.append(f"{digest}  {filename}\n")
    (source / "SHA256SUMS").write_text("".join(lines), encoding="ascii")


def _valid_bundle(tmp_path: Path) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    proof = data_root / "originals" / "inspection" / "proof.bin"
    proof.parent.mkdir(parents=True)
    proof.write_bytes(b"non-sensitive-restore-proof")
    source = tmp_path / "bundle"
    source.mkdir()
    manifest = build_manifest(
        data_root,
        ("originals", "derived", "knowledge", "templates", "documents", "render-previews"),
        application_version="test",
        migration_revision="test:0001",
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    (source / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    (source / "database.dump").write_bytes(b"custom-format-database-placeholder")
    with tarfile.open(source / "files.tar.gz", mode="w:gz") as bundle:
        bundle.add(data_root / "originals", arcname="originals", recursive=True)
    _write_checksums(source)
    return source, proof


def test_verified_bundle_extracts_only_manifest_files_into_empty_root(tmp_path: Path) -> None:
    source, proof = _valid_bundle(tmp_path)
    manifest = verify_backup_bundle(source)
    assert manifest.schema_version == 2
    destination = tmp_path / "restored"
    restore_file_archive(source=source, destination=destination)
    restored = destination / "originals" / "inspection" / "proof.bin"
    assert restored.read_bytes() == proof.read_bytes()
    assert verify_manifest(destination, manifest) == ()


def test_bundle_rejects_symlinks_even_when_outer_checksum_was_recomputed(tmp_path: Path) -> None:
    source, _ = _valid_bundle(tmp_path)
    with tarfile.open(source / "files.tar.gz", mode="w:gz") as bundle:
        directory = tarfile.TarInfo("originals")
        directory.type = tarfile.DIRTYPE
        bundle.addfile(directory)
        link = tarfile.TarInfo("originals/inspection/proof.bin")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        bundle.addfile(link)
    _write_checksums(source)
    with pytest.raises(RestoreValidationError, match="链接或特殊文件"):
        verify_backup_bundle(source)


def test_bundle_rejects_unmanifested_dotenv_payload(tmp_path: Path) -> None:
    source, proof = _valid_bundle(tmp_path)
    with tarfile.open(source / "files.tar.gz", mode="w:gz") as bundle:
        bundle.add(proof.parents[2] / "originals", arcname="originals", recursive=True)
        secret = tarfile.TarInfo("originals/.env.production")
        payload = b"AI_KEY=must-not-be-restored"
        secret.size = len(payload)
        bundle.addfile(secret, io.BytesIO(payload))
    _write_checksums(source)
    with pytest.raises(RestoreValidationError, match="不安全路径"):
        verify_backup_bundle(source)


def test_manifest_rejects_symlinks_and_detects_unexpected_files(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    originals = data_root / "originals"
    originals.mkdir(parents=True)
    expected = originals / "expected.bin"
    expected.write_bytes(b"expected")
    manifest = build_manifest(
        data_root,
        ("originals",),
        application_version="test",
        migration_revision="test:0001",
    )
    (originals / "unexpected.bin").write_bytes(b"unexpected")
    assert "unexpected" in "；".join(verify_manifest(data_root, manifest))
    (originals / "unexpected.bin").unlink()
    (originals / "link.bin").symlink_to(expected)
    with pytest.raises(ValueError, match="符号链接"):
        build_manifest(
            data_root,
            ("originals",),
            application_version="test",
            migration_revision="test:0001",
        )
