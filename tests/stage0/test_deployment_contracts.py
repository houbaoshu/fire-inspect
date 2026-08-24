from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compose_uses_discrete_database_secret_and_hardened_non_root_services() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "postgresql://fire_inspect:${POSTGRES_PASSWORD}" not in compose
    assert "POSTGRES_USER: fire_inspect_bootstrap" in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_ADMIN_PASSWORD:?set POSTGRES_ADMIN_PASSWORD}" in compose
    assert "FIRE_INSPECT_DB_MIGRATION_PASSWORD" in compose
    assert "FIRE_INSPECT_DB_RUNTIME_PASSWORD" in compose
    assert "PGUSER: fire_inspect_migrator" in compose
    assert "PGUSER: fire_inspect_runtime" in compose
    assert "service_completed_successfully" in compose
    assert "FIRE_INSPECT_DATABASE_ROLE: fire_inspect_runtime" in compose
    assert "FIRE_INSPECT_DATABASE_ROLE: fire_inspect_migrator" in compose
    assert 'user: "10001:10001"' in compose
    assert 'user: "101:101"' in compose
    assert 'user: "postgres:postgres"' in compose
    assert compose.count("read_only: true") == 4
    assert compose.count("no-new-privileges:true") == 4
    assert "condition: service_healthy" in compose
    assert "./deploy/fonts:/usr/local/share/fonts/fire-inspect:ro" in compose
    assert "FIRE_INSPECT_REQUIRED_FONTS:" in compose


def test_images_pin_postgres_17_clients_and_nginx_drops_root() -> None:
    application = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    proxy = (ROOT / "deploy/nginx.Dockerfile").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy/nginx.conf").read_text(encoding="utf-8")
    assert "FROM postgres:17.6-bookworm AS postgres-client" in application
    assert "/usr/lib/postgresql/17/bin/pg_dump" in application
    assert "/usr/lib/postgresql/17/bin/pg_restore" in application
    assert "scripts/backup.sh" in application
    assert "scripts/restore.sh" in application
    assert "USER fire-inspect" in application
    assert not any(line.strip().startswith("HOME=") for line in application.splitlines())
    assert "USER nginx" in proxy
    assert "listen 8080;" in nginx
    assert "listen 8443 ssl;" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "location /protected-media/" in nginx
    assert "internal;" in nginx
    nginx_main = (ROOT / "deploy/nginx-main.conf").read_text(encoding="utf-8")
    assert "limit_conn_zone $upload_limit_key zone=upload_concurrency:10m" in nginx_main
    assert "limit_conn upload_concurrency 4;" in nginx


def test_entrypoint_does_not_write_application_root_or_repurpose_home() -> None:
    entrypoint = (ROOT / "scripts/docker-entrypoint.sh").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "$HOME" not in entrypoint
    assert not any(line.strip().startswith("HOME:") for line in compose.splitlines())
    web_case = entrypoint.split("web)", 1)[1].split(";;", 1)[0]
    assert "migrate --noinput" not in web_case
    assert "migrate --check" in web_case
    assert "require_database_role fire_inspect_runtime" in web_case
    assert "migrate)" in entrypoint
    migration_case = entrypoint.split("migrate)", 1)[1].split(";;", 1)[0]
    assert "require_database_role fire_inspect_migrator" in migration_case
    assert "configure_database_privileges" in entrypoint
    assert "fire-inspect-backup" in entrypoint
    assert "fire-inspect-restore" in entrypoint
    assert "FIRE_INSPECT_DATA_ROOT: /data" in compose
    assert "/tmp:rw,noexec,nosuid,nodev" in compose


def test_backup_archive_disables_macos_appledouble_metadata() -> None:
    backup = (ROOT / "scripts/backup.sh").read_text(encoding="utf-8")
    assert "export COPYFILE_DISABLE=1" in backup


def test_docker_build_context_excludes_sensitive_runtime_inputs() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env.*" in dockerignore
    assert "deploy/certs" in dockerignore
    assert "deploy/fonts" in dockerignore
    assert "testdata/*" in dockerignore
    assert "!testdata/knowledge/reg_ec997c582c9c29a6.pdf" in dockerignore


def test_postgres_bootstrap_creates_separate_non_superuser_roles_and_audit_grants() -> None:
    bootstrap = (ROOT / "deploy/postgres-init.sh").read_text(encoding="utf-8")
    assert "CREATE ROLE fire_inspect_migrator" in bootstrap
    assert "CREATE ROLE fire_inspect_runtime" in bootstrap
    assert bootstrap.count("NOSUPERUSER") >= 2
    assert "ALTER DATABASE" in bootstrap
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE fire_inspect_migrator" in bootstrap
    assert "SELECT, INSERT, UPDATE, DELETE" in bootstrap
    assert "--set=migration_password=" in bootstrap
    assert "--set=runtime_password=" in bootstrap
    assert "PASSWORD :'migration_password'" in bootstrap
    assert "PASSWORD :'runtime_password'" in bootstrap
    assert "GRANT ALL" not in bootstrap


def test_database_audit_guard_is_installed_and_runtime_is_never_table_owner() -> None:
    migration = (
        ROOT / "src/fire_inspect/audit/migrations/0003_auditevent_database_guard.py"
    ).read_text(encoding="utf-8")
    privileges = (ROOT / "src/fire_inspect/operations/database_privileges.py").read_text(
        encoding="utf-8"
    )
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "BEFORE TRUNCATE" in migration
    assert "current_user" in migration
    assert "pg_get_userbyid" in migration
    assert "audit_auditevent_append_only_guard" in migration
    assert "REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER" in privileges
    assert "GRANT SELECT, INSERT" in privileges
    assert "GRANT ALL" not in privileges
