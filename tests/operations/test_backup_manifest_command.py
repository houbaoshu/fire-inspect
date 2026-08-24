from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_backup_manifest_command_records_and_verifies_individual_files(
    settings, tmp_path: Path
) -> None:
    data_root = tmp_path / "data"
    artifact = data_root / "documents" / "result.docx"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"document")
    manifest_path = tmp_path / "manifest.json"

    call_command(
        "backup_manifest",
        "create",
        "--data-root",
        str(data_root),
        "--output",
        str(manifest_path),
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["application_version"]
    assert payload["migration_revision"]
    assert payload["entries"][0]["relative_path"] == "documents/result.docx"

    call_command(
        "backup_manifest",
        "verify",
        "--data-root",
        str(data_root),
        "--manifest",
        str(manifest_path),
    )
    artifact.write_bytes(b"tampered")
    with pytest.raises(Exception, match="备份文件校验失败"):
        call_command(
            "backup_manifest",
            "verify",
            "--data-root",
            str(data_root),
            "--manifest",
            str(manifest_path),
        )
