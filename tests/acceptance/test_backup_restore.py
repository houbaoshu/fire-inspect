from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.models import Session
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.utils import timezone
from psycopg import sql
from tests.inspections.test_inspection_api import create_inspection

from fire_inspect.documents.models import DocumentRevision, InspectionDocument
from fire_inspect.materials.services import create_text_material
from fire_inspect.operations.database_privileges import MIGRATION_ROLE, RUNTIME_ROLE
from fire_inspect.templates.models import (
    TemplateMappingRevision,
    TemplateVersion,
    WordTemplate,
)

ROOT = Path(__file__).resolve().parents[2]


def _database_url(base_url: str, database_name: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, "/" + quote(database_name, safe=""), parsed.query, "")
    )


def _role_database_url(base_url: str, database_name: str, *, role: str, password: str) -> str:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{hostname}{port}"
    return urlunsplit(
        (parsed.scheme, netloc, "/" + quote(database_name, safe=""), parsed.query, "")
    )


def _production_environment(
    *, database_url: str, data_root: Path, database_role: str = ""
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "fire_inspect.config.settings",
            "PYTHONPATH": str(ROOT / "src"),
            "FIRE_INSPECT_ENV": "production",
            "FIRE_INSPECT_DEBUG": "0",
            "FIRE_INSPECT_SECRET_KEY": "restore-test-9fA2!" * 5,
            "FIRE_INSPECT_ALLOWED_HOSTS": "fire-inspect.test",
            "FIRE_INSPECT_PUBLIC_BASE_URL": "https://fire-inspect.test",
            "FIRE_INSPECT_CSRF_TRUSTED_ORIGINS": "https://fire-inspect.test",
            "FIRE_INSPECT_COOKIE_SECURE": "1",
            "FIRE_INSPECT_DATABASE_URL": database_url,
            "FIRE_INSPECT_DATABASE_ROLE": database_role,
            "FIRE_INSPECT_DATA_ROOT": str(data_root),
            "FIRE_INSPECT_MIN_FREE_BYTES": "0",
        }
    )
    return environment


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_consistent_backup_restores_database_and_files_into_empty_environment(
    inspector_a,
    settings,
    tmp_path: Path,
) -> None:
    base_url = os.environ["FIRE_INSPECT_POSTGRES_TEST_URL"]
    current_name = str(connection.settings_dict["NAME"])
    source_url = _database_url(base_url, current_name)
    admin_url = _database_url(base_url, "postgres")
    restore_name = f"fire_inspect_restore_{uuid4().hex[:16]}"
    assert restore_name.startswith("fire_inspect_restore_")
    migration_password = "Restore-Migrate@:/?#%+Password-42!"
    runtime_password = "Restore-Runtime@:/?#%+Password-42!"
    restore_url = _role_database_url(
        base_url,
        restore_name,
        role=MIGRATION_ROLE,
        password=migration_password,
    )

    smoke_password = "Restore-Smoke-User-Password-42!"
    inspector_a.password = make_password(smoke_password, hasher="pbkdf2_sha256")
    inspector_a.save(update_fields=("password",))
    inspection = create_inspection(inspector_a)
    Session.objects.create(
        session_key="active-session-must-not-enter-backup",
        session_data="sensitive-active-session-placeholder",
        expire_date=timezone.now() + timedelta(hours=1),
    )
    source_root = tmp_path / "source-data"
    settings.MEDIA_ROOT = source_root
    settings.FIRE_INSPECT_STAGING_ROOT = source_root / "staging"
    settings.FIRE_INSPECT_QUARANTINE_ROOT = source_root / "quarantine" / "deletions"
    material_text = "post-restore controlled material proof"
    material, material_created = create_text_material(
        inspection=inspection,
        actor=inspector_a,
        display_name="restore-proof.txt",
        text=material_text,
    )
    assert material_created is True

    document_payload = (ROOT / "docs/templates/监督检查记录表.docx").read_bytes()
    document_sha256 = hashlib.sha256(document_payload).hexdigest()
    template = WordTemplate.objects.create(
        document_type=WordTemplate.Type.RECORD,
        name="恢复验收模板",
        recognition_status=WordTemplate.RecognitionStatus.SUCCEEDED,
        created_by=inspector_a,
    )
    template_version = TemplateVersion(
        template=template,
        version_no=1,
        original_filename="监督检查记录表.docx",
        sha256=document_sha256,
        validation_result={"passed": True},
        created_by=inspector_a,
    )
    template_version.original_file.save("original.docx", ContentFile(document_payload), save=False)
    template_version.save(force_insert=True)
    mapping = TemplateMappingRevision.objects.create(
        template_version=template_version,
        revision_no=1,
        state=TemplateMappingRevision.State.CONFIRMED,
        recognizer_version="restore-smoke-v1",
        created_by=inspector_a,
        confirmed_by=inspector_a,
        confirmed_at=timezone.now(),
    )
    template.active_mapping_revision = mapping
    template.save(update_fields=("active_mapping_revision",))
    document = InspectionDocument.objects.create(
        inspection=inspection,
        document_type=InspectionDocument.Type.RECORD,
        name="恢复验收消防监督检查记录",
        template_version=template_version,
        current_mapping_revision=mapping,
        state=InspectionDocument.State.CONFIRMED,
        generator_status=InspectionDocument.GeneratorStatus.SUCCEEDED,
        generation_valid=True,
        confirmed_by=inspector_a,
        confirmed_at=timezone.now(),
        created_by=inspector_a,
    )
    generated_storage_key = default_storage.save(
        f"documents/{document.id}/restore-smoke/document.docx",
        ContentFile(document_payload),
    )
    generated_revision = DocumentRevision.objects.create(
        document=document,
        revision_no=1,
        document_revision_no=document.revision,
        event_type=DocumentRevision.EventType.GENERATED,
        content_snapshot={"restore_smoke": True},
        input_revision_vector={},
        mapping_revision=mapping,
        generator_version=document.generator_version,
        generated_storage_key=generated_storage_key,
        generated_sha256=document_sha256,
        page_count=1,
        structure_check_result={"passed": True},
        render_check_result={"passed": True},
        created_by=inspector_a,
    )
    document.last_generated_revision = generated_revision
    document.save(update_fields=("last_generated_revision", "updated_at"))

    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    restore_root = tmp_path / "restored-data"

    with psycopg.connect(admin_url, autocommit=True) as admin:
        existing_roles = admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            ([MIGRATION_ROLE, RUNTIME_ROLE],),
        ).fetchall()
        if existing_roles:
            pytest.skip("backup/restore role test requires a dedicated PostgreSQL cluster")
        for role, password in (
            (MIGRATION_ROLE, migration_password),
            (RUNTIME_ROLE, runtime_password),
        ):
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(restore_name), sql.Identifier(MIGRATION_ROLE)
            )
        )
    with psycopg.connect(_database_url(base_url, restore_name), autocommit=True) as admin:
        admin.execute(
            sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(MIGRATION_ROLE))
        )
    try:
        backup_environment = _production_environment(
            database_url=source_url,
            data_root=source_root,
        )
        backup_environment["FIRE_INSPECT_BACKUP_DIR"] = str(backup_root)
        backup_environment["FIRE_INSPECT_MAINTENANCE_FILE"] = str(source_root / ".maintenance")
        backup = subprocess.run(
            ["sh", "scripts/backup.sh"],
            cwd=ROOT,
            env=backup_environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert backup.returncode == 0
        backup_targets = [path for path in backup_root.iterdir() if path.is_dir()]
        assert len(backup_targets) == 1
        backup_target = backup_targets[0]

        restore_environment = _production_environment(
            database_url=restore_url,
            data_root=restore_root,
            database_role=MIGRATION_ROLE,
        )
        restore_environment.update(
            {
                "FIRE_INSPECT_RESTORE_SOURCE": str(backup_target),
                "FIRE_INSPECT_RESTORE_ACK": "RESTORE_IN_EMPTY_TEST_ENVIRONMENT",
            }
        )
        restore = subprocess.run(
            ["sh", "scripts/restore.sh"],
            cwd=ROOT,
            env=restore_environment,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert restore.returncode == 0
        with psycopg.connect(restore_url) as restored:
            row = restored.execute(
                "SELECT unit_name FROM inspections_inspection WHERE id = %s",
                (inspection.id,),
            ).fetchone()
            restored_session_count = restored.execute(
                "SELECT count(*) FROM django_session"
            ).fetchone()[0]
        assert row == (inspection.unit_name,)
        assert restored_session_count == 0

        runtime_url = _role_database_url(
            base_url,
            restore_name,
            role=RUNTIME_ROLE,
            password=runtime_password,
        )
        runtime_environment = _production_environment(
            database_url=runtime_url,
            data_root=restore_root,
            database_role=RUNTIME_ROLE,
        )
        runtime_environment.update(
            {
                "FIRE_INSPECT_SMOKE_USERNAME": inspector_a.username,
                "FIRE_INSPECT_SMOKE_PASSWORD": smoke_password,
                "FIRE_INSPECT_SMOKE_INSPECTION_ID": str(inspection.id),
                "FIRE_INSPECT_SMOKE_MATERIAL_ID": str(material.id),
                "FIRE_INSPECT_SMOKE_MATERIAL_SHA256": material.sha256,
                "FIRE_INSPECT_SMOKE_DOCUMENT_ID": str(document.id),
                "FIRE_INSPECT_SMOKE_DOCUMENT_SHA256": document_sha256,
            }
        )
        smoke = subprocess.run(
            [sys.executable, "manage.py", "post_restore_smoke"],
            cwd=ROOT,
            env=runtime_environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert smoke.returncode == 0, "post-restore runtime smoke failed"
        assert smoke.stdout.strip() == "post-restore runtime smoke passed"
        command_output = smoke.stdout + smoke.stderr
        for forbidden in (
            migration_password,
            runtime_password,
            smoke_password,
            str(source_root),
            str(restore_root),
            material_text,
        ):
            assert forbidden not in command_output
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (restore_name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(restore_name)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(RUNTIME_ROLE)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(MIGRATION_ROLE)))
