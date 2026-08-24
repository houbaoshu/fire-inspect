from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from fire_inspect.operations.database_privileges import (
    AUDIT_TABLE,
    MIGRATION_ROLE,
    RUNTIME_ROLE,
)

ROOT = Path(__file__).resolve().parents[2]


def _connection_kwargs(url: str, *, database: str | None = None) -> dict[str, object]:
    parsed = urlsplit(url)
    return {
        "host": parsed.hostname or "",
        "port": parsed.port or 5432,
        "dbname": database or unquote(parsed.path.lstrip("/")),
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }


def _application_environment(
    *, database_name: str, password: str, data_root: Path
) -> dict[str, str]:
    base = _connection_kwargs(os.environ["FIRE_INSPECT_POSTGRES_TEST_URL"])
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "fire_inspect.config.settings",
            "PYTHONPATH": str(ROOT / "src"),
            "FIRE_INSPECT_ENV": "production",
            "FIRE_INSPECT_DEBUG": "0",
            "FIRE_INSPECT_SECRET_KEY": "postgres-role-test-9fA2!" * 4,
            "FIRE_INSPECT_ALLOWED_HOSTS": "fire-inspect.test",
            "FIRE_INSPECT_PUBLIC_BASE_URL": "https://fire-inspect.test",
            "FIRE_INSPECT_CSRF_TRUSTED_ORIGINS": "https://fire-inspect.test",
            "FIRE_INSPECT_COOKIE_SECURE": "1",
            "FIRE_INSPECT_DATABASE_ROLE": MIGRATION_ROLE,
            "FIRE_INSPECT_DATABASE_URL": "",
            "PGHOST": str(base["host"]),
            "PGPORT": str(base["port"]),
            "PGDATABASE": database_name,
            "PGUSER": MIGRATION_ROLE,
            "PGPASSWORD": password,
            "FIRE_INSPECT_DATA_ROOT": str(data_root),
            "FIRE_INSPECT_MIN_FREE_BYTES": "0",
        }
    )
    return environment


@pytest.mark.postgres
def test_postgres_runtime_role_cannot_mutate_permanent_audit_events(tmp_path: Path) -> None:
    base_url = os.environ["FIRE_INSPECT_POSTGRES_TEST_URL"]
    admin_kwargs = _connection_kwargs(base_url, database="postgres")
    database_name = f"fire_inspect_roles_{uuid4().hex[:16]}"
    migration_password = "Migrate@:/?#%+Password-42!"
    runtime_password = "Runtime@:/?#%+Password-42!"
    created_database = False
    cleanup_roles = False

    with psycopg.connect(**admin_kwargs, autocommit=True) as admin:
        existing = admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            ([MIGRATION_ROLE, RUNTIME_ROLE],),
        ).fetchall()
        if existing:
            pytest.skip("role-isolation test requires a dedicated PostgreSQL cluster")
        cleanup_roles = True
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        created_database = True

    try:
        bootstrap = os.environ.copy()
        bootstrap.update(
            {
                "POSTGRES_DB": database_name,
                "POSTGRES_USER": str(admin_kwargs["user"]),
                "PGHOST": str(admin_kwargs["host"]),
                "PGPORT": str(admin_kwargs["port"]),
                "PGPASSWORD": str(admin_kwargs["password"]),
                "FIRE_INSPECT_DB_MIGRATION_PASSWORD": migration_password,
                "FIRE_INSPECT_DB_RUNTIME_PASSWORD": runtime_password,
            }
        )
        initialized = subprocess.run(
            ["sh", "deploy/postgres-init.sh"],
            cwd=ROOT,
            env=bootstrap,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert initialized.returncode == 0, "postgres role bootstrap failed"

        application_environment = _application_environment(
            database_name=database_name,
            password=migration_password,
            data_root=tmp_path / "data",
        )
        for arguments in (
            [sys.executable, "manage.py", "migrate", "--noinput"],
            [sys.executable, "manage.py", "configure_database_privileges"],
        ):
            completed = subprocess.run(
                arguments,
                cwd=ROOT,
                env=application_environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            assert completed.returncode == 0, "migration privilege setup failed"

        runtime_environment = application_environment.copy()
        runtime_environment.update(
            {
                "PGUSER": RUNTIME_ROLE,
                "PGPASSWORD": runtime_password,
                "FIRE_INSPECT_DATABASE_ROLE": RUNTIME_ROLE,
            }
        )
        appended = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "shell",
                "-c",
                (
                    "from fire_inspect.audit.models import AuditEvent; "
                    "from fire_inspect.operations import validate_database_role; "
                    "validate_database_role(expected_role='fire_inspect_runtime'); "
                    "AuditEvent.append(actor=None, object_type='security', "
                    "object_id=None, action='runtime_append', "
                    "masked_change_summary='permission test')"
                ),
            ],
            cwd=ROOT,
            env=runtime_environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert appended.returncode == 0, "runtime audit append failed"

        runtime_kwargs = {
            **admin_kwargs,
            "dbname": database_name,
            "user": RUNTIME_ROLE,
            "password": runtime_password,
        }
        audit_relation = f"public.{AUDIT_TABLE}"
        event_id = uuid4()
        with psycopg.connect(**runtime_kwargs, autocommit=True) as runtime:
            role = runtime.execute(
                "SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
            privileges = runtime.execute(
                """
                SELECT has_table_privilege(current_user, %s, 'SELECT'),
                       has_table_privilege(current_user, %s, 'INSERT'),
                       has_table_privilege(current_user, %s, 'UPDATE'),
                       has_table_privilege(current_user, %s, 'DELETE'),
                       has_table_privilege(current_user, %s, 'TRUNCATE'),
                       has_table_privilege(current_user, %s, 'REFERENCES'),
                       has_table_privilege(current_user, %s, 'TRIGGER'),
                       has_table_privilege(current_user, %s, 'MAINTAIN')
                """,
                [audit_relation] * 8,
            ).fetchone()
            assert role == (RUNTIME_ROLE, False)
            assert privileges == (True, True, False, False, False, False, False, False)
            runtime.execute(
                """
                INSERT INTO public.audit_auditevent (
                    id, actor_id, actor_label, occurred_at, object_type, object_id,
                    inspection_id_snapshot, action, reason, masked_change_summary,
                    request_id, previous_hash, event_hash
                ) VALUES (
                    %s, NULL, 'permission-test', CURRENT_TIMESTAMP, 'security', NULL,
                    NULL, 'created', '', '', '', '', %s
                )
                """,
                (event_id, uuid4().hex),
            )
            assert runtime.execute(
                "SELECT action FROM public.audit_auditevent WHERE id = %s", (event_id,)
            ).fetchone() == ("created",)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(
                    "UPDATE public.audit_auditevent SET action = 'tampered' WHERE id = %s",
                    (event_id,),
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute("DELETE FROM public.audit_auditevent WHERE id = %s", (event_id,))

        database_admin_kwargs = {**admin_kwargs, "dbname": database_name}
        with psycopg.connect(**database_admin_kwargs, autocommit=True) as admin:
            admin.execute(
                sql.SQL("GRANT UPDATE, DELETE, TRUNCATE ON {} TO {}").format(
                    sql.Identifier("public", AUDIT_TABLE), sql.Identifier(RUNTIME_ROLE)
                )
            )
        with psycopg.connect(**runtime_kwargs, autocommit=True) as runtime:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                runtime.execute(
                    "UPDATE public.audit_auditevent SET action = 'tampered' WHERE id = %s",
                    (event_id,),
                )
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                runtime.execute("DELETE FROM public.audit_auditevent WHERE id = %s", (event_id,))
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                runtime.execute("TRUNCATE public.audit_auditevent")
    finally:
        with psycopg.connect(**admin_kwargs, autocommit=True) as admin:
            if created_database:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
                )
            if cleanup_roles:
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(RUNTIME_ROLE))
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(MIGRATION_ROLE))
                )
