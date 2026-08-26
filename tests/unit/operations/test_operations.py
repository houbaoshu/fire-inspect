import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from fire_inspect.operations import (
    PreflightError,
    ProductionConfiguration,
    SensitiveDataFilter,
    build_manifest,
    redact_text,
    run_preflight,
    validate_ai_environment,
    validate_database_role,
    validate_postgres_runtime,
    validate_required_fonts,
    verify_manifest,
)
from fire_inspect.operations.management.commands.post_restore_smoke import (
    PostRestoreSmokeConfiguration,
    PostRestoreSmokeError,
)


def valid_config(tmp_path: Path) -> ProductionConfiguration:
    return ProductionConfiguration(
        secret_key="Strong-Random-Canary-" * 4,
        allowed_hosts=("fire-inspect.internal",),
        public_base_url="https://fire-inspect.internal",
        data_root=tmp_path / "data",
        cookie_secure=True,
        debug=False,
        database_url="postgresql://fire_inspect:<redacted>@db/fire_inspect",
    )


def test_production_preflight_rejects_insecure_defaults(tmp_path: Path) -> None:
    config = ProductionConfiguration("short", ("*",), "http://host", tmp_path, False, True, "")
    with pytest.raises(PreflightError) as error:
        run_preflight(config, require_document_renderer=False)
    message = str(error.value)
    assert "HTTPS" in message
    assert "Secure" in message
    assert "PostgreSQL" in message


def test_preflight_repr_never_contains_database_or_secret(tmp_path: Path) -> None:
    config = valid_config(tmp_path)
    assert config.secret_key not in repr(config)
    assert config.database_url not in repr(config)
    assert str(config.data_root) not in repr(config)


def test_preflight_accepts_discrete_database_password_with_url_reserved_characters(
    tmp_path: Path,
) -> None:
    config = ProductionConfiguration(
        secret_key="Strong-Random-Canary-" * 4,
        allowed_hosts=("fire-inspect.internal",),
        public_base_url="https://fire-inspect.internal",
        data_root=tmp_path / "data",
        cookie_secure=True,
        debug=False,
        database_url="",
        database_name="fire_inspect",
        database_user="fire_inspect",
        database_password="Strong@:/?#%+Database-Password-42!",
        database_host="db",
    )
    assert config.database_parts() == (
        "fire_inspect",
        "fire_inspect",
        "Strong@:/?#%+Database-Password-42!",
        "db",
        "5432",
    )
    assert config.database_password not in repr(config)


def test_post_restore_smoke_configuration_is_strict_and_redacts_all_inputs() -> None:
    password = "Restore-Smoke-Secret-42!"
    identifiers = [str(uuid4()) for _ in range(3)]
    environment = {
        "FIRE_INSPECT_SMOKE_USERNAME": "restore-inspector",
        "FIRE_INSPECT_SMOKE_PASSWORD": password,
        "FIRE_INSPECT_SMOKE_INSPECTION_ID": identifiers[0],
        "FIRE_INSPECT_SMOKE_MATERIAL_ID": identifiers[1],
        "FIRE_INSPECT_SMOKE_MATERIAL_SHA256": "a" * 64,
        "FIRE_INSPECT_SMOKE_DOCUMENT_ID": identifiers[2],
        "FIRE_INSPECT_SMOKE_DOCUMENT_SHA256": "b" * 64,
    }

    configuration = PostRestoreSmokeConfiguration.from_environment(environment)

    representation = repr(configuration)
    for secret in (password, environment["FIRE_INSPECT_SMOKE_USERNAME"], *identifiers):
        assert secret not in representation
    with pytest.raises(PostRestoreSmokeError, match="configuration"):
        PostRestoreSmokeConfiguration.from_environment(
            {**environment, "FIRE_INSPECT_SMOKE_MATERIAL_SHA256": "not-a-sha256"}
        )


def test_postgres_runtime_requires_server_and_both_clients_on_major_17(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query):
            assert query == "SHOW server_version_num"

        def fetchone(self):
            return ("170006",)

    class Connection:
        vendor = "postgresql"

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        "fire_inspect.operations.preflight._postgres_client_major", lambda binary: 17
    )
    assert validate_postgres_runtime(Connection()) == {
        "server": 17,
        "pg_dump": 17,
        "pg_restore": 17,
    }
    monkeypatch.setattr(
        "fire_inspect.operations.preflight._postgres_client_major",
        lambda binary: 16 if binary == "pg_dump" else 17,
    )
    with pytest.raises(PreflightError, match="pg_dump"):
        validate_postgres_runtime(Connection())


def test_database_role_validation_requires_non_superuser_and_exact_audit_grants() -> None:
    class Cursor:
        row = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=None):
            statement = str(query)
            if "FROM pg_roles" in statement:
                self.row = ("fire_inspect_runtime", False, False, False, False, False)
            elif "pg_get_userbyid" in statement:
                self.row = ("audit_auditevent", "fire_inspect_migrator")
            elif "has_table_privilege" in statement:
                self.row = (True, True, False, False, False, False, False, False)
            elif "FROM pg_trigger" in statement:
                self.row = (True,)
            else:  # pragma: no cover - makes an unexpected query obvious
                raise AssertionError(statement)

        def fetchone(self):
            return self.row

    class Connection:
        vendor = "postgresql"

        def cursor(self):
            return Cursor()

    assert validate_database_role(Connection(), expected_role="fire_inspect_runtime") == {
        "role": "fire_inspect_runtime",
        "superuser": False,
        "audit_append_only": True,
    }


def test_database_role_validation_rejects_superuser_and_audit_update_grant() -> None:
    class Cursor:
        rows = iter(
            (
                ("fire_inspect_runtime", True, False, False, False, False),
                ("fire_inspect_runtime", False, False, False, False, False),
                ("audit_auditevent", "fire_inspect_migrator"),
                (True, True, True, False, False, False, False, False),
            )
        )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params=None):
            self.row = next(self.rows)

        def fetchone(self):
            return self.row

    class Connection:
        vendor = "postgresql"

        def cursor(self):
            return Cursor()

    connection = Connection()
    with pytest.raises(PreflightError, match="不能是超级用户"):
        validate_database_role(connection, expected_role="fire_inspect_runtime")
    with pytest.raises(PreflightError, match="仅允许 SELECT 和 INSERT"):
        validate_database_role(connection, expected_role="fire_inspect_runtime")


def test_production_font_gate_requires_exact_authoritative_template_families() -> None:
    required = ("FangSong", "KaiTi", "Microsoft YaHei UI", "SimSun")
    assert validate_required_fonts(required, available_fonts=set(required)) == required
    with pytest.raises(PreflightError, match="KaiTi"):
        validate_required_fonts(required, available_fonts={"FangSong", "Noto Sans CJK SC"})


def test_production_preflight_rejects_fixed_or_incomplete_ai_provider() -> None:
    with pytest.raises(PreflightError, match="禁止 fixed"):
        validate_ai_environment({"FIRE_INSPECT_ASR_PROVIDER": "fixed"})
    with pytest.raises(PreflightError, match="配置不完整"):
        validate_ai_environment({"FIRE_INSPECT_LLM_PROVIDER": "http"})
    validate_ai_environment(
        {
            "FIRE_INSPECT_VISION_PROVIDER": "http",
            "FIRE_INSPECT_VISION_ENDPOINT": "https://gateway.internal/vision",
            "FIRE_INSPECT_VISION_MODEL": "approved-model",
            "FIRE_INSPECT_VISION_API_KEY": "secret-not-printed",
        }
    )


def test_production_preflight_rejects_font_substitution() -> None:
    with pytest.raises(PreflightError, match="字体替代"):
        validate_ai_environment({"FIRE_INSPECT_ALLOW_FONT_SUBSTITUTION": "1"})
    validate_ai_environment({"FIRE_INSPECT_ALLOW_FONT_SUBSTITUTION": "0"})


def test_log_redaction_masks_all_canaries() -> None:
    message = (
        "Authorization: Bearer TOKEN-CANARY sessionid=COOKIE-CANARY "
        "api_key=KEY-CANARY phone=13812345678 id=330102199001011234 "
        "/srv/fire-inspect/data/originals/a"
    )
    redacted = redact_text(message, sensitive_roots=["/srv/fire-inspect/data"])
    for canary in (
        "TOKEN-CANARY",
        "COOKIE-CANARY",
        "KEY-CANARY",
        "13812345678",
        "330102199001011234",
    ):
        assert canary not in redacted
    assert "/srv/fire-inspect/data" not in redacted


def test_logging_filter_replaces_formatted_message() -> None:
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "cookie=%s", ("COOKIE",), None)
    assert SensitiveDataFilter().filter(record)
    assert record.args == ()
    assert "COOKIE" not in record.getMessage()


def test_backup_manifest_detects_tampering_and_excludes_env(tmp_path: Path) -> None:
    (tmp_path / "originals").mkdir()
    artifact = tmp_path / "originals" / "item.bin"
    artifact.write_bytes(b"immutable")
    (tmp_path / "originals" / ".env").write_text("SECRET=do-not-back-up")
    manifest = build_manifest(
        tmp_path,
        ["originals"],
        application_version="0.1.0",
        migration_revision="0001",
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert [entry.relative_path for entry in manifest.entries] == ["originals/item.bin"]
    assert verify_manifest(tmp_path, manifest) == ()
    artifact.write_bytes(b"changed")
    assert "size mismatch" in verify_manifest(tmp_path, manifest)[0]
