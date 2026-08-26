from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from fire_inspect.operations.database_privileges import (
    APPLICATION_SCHEMA,
    AUDIT_TABLE,
    AUDIT_TRIGGER,
    AUDIT_TRUNCATE_TRIGGER,
    MIGRATION_ROLE,
    RUNTIME_ROLE,
)


class PreflightError(RuntimeError):
    pass


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True, repr=False)
class ProductionConfiguration:
    secret_key: str
    allowed_hosts: tuple[str, ...]
    public_base_url: str
    data_root: Path
    cookie_secure: bool
    debug: bool
    database_url: str
    database_name: str = ""
    database_user: str = ""
    database_password: str = ""
    database_host: str = ""
    database_port: str = "5432"
    min_free_bytes: int = 1024**3
    required_fonts: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            "ProductionConfiguration(secret_key=<redacted>, "
            f"allowed_hosts={self.allowed_hosts!r}, public_base_url={self.public_base_url!r}, "
            f"data_root=<redacted>, cookie_secure={self.cookie_secure!r}, "
            f"debug={self.debug!r}, database=<redacted>)"
        )

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> ProductionConfiguration:
        source = os.environ if environment is None else environment
        try:
            min_free_bytes = int(source.get("FIRE_INSPECT_MIN_FREE_BYTES", str(1024**3)))
        except ValueError as exc:
            raise PreflightError("数据目录最小剩余空间配置无效") from exc
        return cls(
            secret_key=source.get("FIRE_INSPECT_SECRET_KEY", ""),
            allowed_hosts=tuple(
                item.strip()
                for item in source.get("FIRE_INSPECT_ALLOWED_HOSTS", "").split(",")
                if item.strip()
            ),
            public_base_url=source.get("FIRE_INSPECT_PUBLIC_BASE_URL", ""),
            data_root=Path(source.get("FIRE_INSPECT_DATA_ROOT", "data")),
            cookie_secure=_truthy(source.get("FIRE_INSPECT_COOKIE_SECURE", "true")),
            debug=_truthy(source.get("FIRE_INSPECT_DEBUG", "false")),
            database_url=source.get("FIRE_INSPECT_DATABASE_URL", ""),
            database_name=source.get("PGDATABASE", ""),
            database_user=source.get("PGUSER", ""),
            database_password=source.get("PGPASSWORD", ""),
            database_host=source.get("PGHOST", ""),
            database_port=source.get("PGPORT", "5432"),
            min_free_bytes=min_free_bytes,
            required_fonts=tuple(
                item.strip()
                for item in source.get("FIRE_INSPECT_REQUIRED_FONTS", "").split(",")
                if item.strip()
            ),
        )

    def database_parts(self) -> tuple[str, str, str, str, str] | None:
        if self.database_url:
            try:
                parsed = urlparse(self.database_url)
                port = str(parsed.port or 5432)
            except ValueError:
                return None
            if parsed.scheme not in {"postgres", "postgresql"}:
                return None
            values = (
                unquote(parsed.path.lstrip("/")),
                unquote(parsed.username or ""),
                unquote(parsed.password or ""),
                parsed.hostname or "",
                port,
            )
            return values if all(values) else None
        values = (
            self.database_name,
            self.database_user,
            self.database_password,
            self.database_host,
            self.database_port,
        )
        return values if all(values) else None


_POSTGRES_VERSION_RE = re.compile(r"\(PostgreSQL\)\s+(\d+)(?:\.\d+)?")
EXPECTED_POSTGRES_MAJOR = 17


def _postgres_client_major(binary: str) -> int | None:
    executable = shutil.which(binary)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _POSTGRES_VERSION_RE.search(completed.stdout)
    return int(match.group(1)) if match else None


def _normalized_font_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def discover_font_families() -> set[str]:
    executable = shutil.which("fc-list")
    if executable is None:
        return set()
    try:
        completed = subprocess.run(
            [executable, "-f", "%{family}\n"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    families: set[str] = set()
    for line in completed.stdout.splitlines():
        for family in line.replace("\\-", "-").split(","):
            if family.strip():
                families.add(family.strip())
    return families


def validate_required_fonts(
    required_fonts: tuple[str, ...], *, available_fonts: set[str] | None = None
) -> tuple[str, ...]:
    if not required_fonts:
        return ()
    available = available_fonts if available_fonts is not None else discover_font_families()
    normalized_available = {_normalized_font_name(item) for item in available}
    missing = tuple(
        font for font in required_fonts if _normalized_font_name(font) not in normalized_available
    )
    if missing:
        raise PreflightError("缺少模板指定的授权字体: " + "、".join(missing))
    return required_fonts


def validate_database_role(
    database_connection=None, *, expected_role: str
) -> dict[str, str | bool]:
    """Fail closed unless PostgreSQL is using the expected non-superuser role."""

    if database_connection is None:
        from django.db import connection as database_connection

    if expected_role not in {MIGRATION_ROLE, RUNTIME_ROLE}:
        raise PreflightError("生产环境数据库角色配置无效")
    try:
        if database_connection.vendor != "postgresql":
            raise PreflightError("生产环境数据库必须是 PostgreSQL 17")
        with database_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,
                       role_info.rolsuper,
                       role_info.rolcreatedb,
                       role_info.rolcreaterole,
                       role_info.rolreplication,
                       role_info.rolbypassrls
                  FROM pg_roles AS role_info
                 WHERE role_info.rolname = current_user
                """
            )
            role_row = cursor.fetchone()
            if role_row is None or str(role_row[0]) != expected_role:
                raise PreflightError("数据库连接角色与部署职责不匹配")
            if bool(role_row[1]):
                raise PreflightError("应用数据库角色不能是超级用户")
            if any(bool(value) for value in role_row[2:]):
                raise PreflightError("应用数据库角色不能拥有管理或绕过权限")

            if expected_role == MIGRATION_ROLE:
                return {
                    "role": expected_role,
                    "superuser": False,
                    "audit_append_only": False,
                }

            audit_relation = f"{APPLICATION_SCHEMA}.{AUDIT_TABLE}"
            cursor.execute(
                """
                SELECT relation_info.oid::regclass::text,
                       pg_get_userbyid(relation_info.relowner)
                  FROM pg_class AS relation_info
                 WHERE relation_info.oid = to_regclass(%s)
                """,
                [audit_relation],
            )
            audit_row = cursor.fetchone()
            if audit_row is None or str(audit_row[1]) != MIGRATION_ROLE:
                raise PreflightError("审计表必须由数据库迁移角色持有")

            cursor.execute(
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
            )
            privileges = tuple(bool(value) for value in cursor.fetchone())
            if privileges != (True, True, False, False, False, False, False, False):
                raise PreflightError("运行时角色对审计表仅允许 SELECT 和 INSERT")

            cursor.execute(
                """
                SELECT COUNT(*) = 2
                  FROM pg_trigger AS trigger_info
                  JOIN pg_class AS relation_info
                    ON relation_info.oid = trigger_info.tgrelid
                  JOIN pg_proc AS function_info
                    ON function_info.oid = trigger_info.tgfoid
                 WHERE relation_info.oid = to_regclass(%s)
                   AND trigger_info.tgname IN (%s, %s)
                   AND function_info.proname = 'fire_inspect_guard_audit_append_only'
                   AND NOT trigger_info.tgisinternal
                   AND trigger_info.tgenabled IN ('O', 'A')
                """,
                [audit_relation, AUDIT_TRIGGER, AUDIT_TRUNCATE_TRIGGER],
            )
            if not bool(cursor.fetchone()[0]):
                raise PreflightError("审计表数据库防篡改触发器缺失")
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("无法核验生产数据库角色及审计权限") from exc
    return {
        "role": expected_role,
        "superuser": False,
        "audit_append_only": True,
    }


def validate_postgres_runtime(
    database_connection=None, *, expected_role: str | None = None
) -> dict[str, int | str | bool]:
    """Require PostgreSQL 17 on both sides without echoing connection details."""

    if database_connection is None:
        from django.db import connection as database_connection

    try:
        if database_connection.vendor != "postgresql":
            raise PreflightError("生产环境数据库必须是 PostgreSQL 17")
        with database_connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            raw_server_version = str(cursor.fetchone()[0])
        server_major = int(raw_server_version) // 10000
    except PreflightError:
        raise
    except Exception as exc:
        raise PreflightError("无法连接并核验 PostgreSQL 服务端版本") from exc
    client_majors = {binary: _postgres_client_major(binary) for binary in ("pg_dump", "pg_restore")}
    failures: list[str] = []
    if server_major != EXPECTED_POSTGRES_MAJOR:
        failures.append("PostgreSQL 服务端必须使用 17.x")
    for binary, major in client_majors.items():
        if major != EXPECTED_POSTGRES_MAJOR:
            failures.append(f"{binary} 必须使用 PostgreSQL 17.x 客户端")
    if failures:
        raise PreflightError("；".join(failures))
    result: dict[str, int | str | bool] = {
        "server": server_major,
        "pg_dump": int(client_majors["pg_dump"]),
        "pg_restore": int(client_majors["pg_restore"]),
    }
    if expected_role is not None:
        result.update(validate_database_role(database_connection, expected_role=expected_role))
    return result


def run_preflight(
    configuration: ProductionConfiguration,
    *,
    require_document_renderer: bool = True,
) -> dict[str, object]:
    failures: list[str] = []
    if len(configuration.secret_key) < 50 or len(set(configuration.secret_key)) < 8:
        failures.append("必须配置至少 50 字符的强随机应用密钥")
    if not configuration.allowed_hosts or any("*" in host for host in configuration.allowed_hosts):
        failures.append("必须配置明确的可信 Host")
    parsed_public_url = urlparse(configuration.public_base_url)
    if (
        parsed_public_url.scheme.lower() != "https"
        or not parsed_public_url.hostname
        or parsed_public_url.username
        or parsed_public_url.password
        or parsed_public_url.query
        or parsed_public_url.fragment
        or parsed_public_url.path not in {"", "/"}
    ):
        failures.append("生产环境必须使用 HTTPS 公网基址")
    elif parsed_public_url.hostname not in {
        host.removeprefix(".").split(":", 1)[0] for host in configuration.allowed_hosts
    }:
        failures.append("HTTPS 公网基址必须属于可信 Host")
    if not configuration.cookie_secure:
        failures.append("生产环境会话 Cookie 必须启用 Secure")
    if configuration.debug:
        failures.append("生产环境不能启用 DEBUG")
    database_parts = configuration.database_parts()
    if database_parts is None:
        failures.append("生产环境必须配置完整的 PostgreSQL 连接参数")
    else:
        _, _, database_password, _, database_port = database_parts
        if len(database_password) < 16 or len(set(database_password)) < 6:
            failures.append("必须配置至少 16 字符的强随机数据库密码")
        if database_password.upper().startswith("REPLACE_WITH_"):
            failures.append("数据库密码不能使用示例占位值")
        try:
            if not 1 <= int(database_port) <= 65535:
                raise ValueError
        except ValueError:
            failures.append("PostgreSQL 数据库端口无效")
    if configuration.data_root.resolve() in {Path("/").resolve(), Path.home().resolve()}:
        failures.append("数据目录不能是根目录或用户主目录")
    if configuration.min_free_bytes < 0:
        failures.append("数据目录最小剩余空间配置无效")
    try:
        configuration.data_root.mkdir(parents=True, exist_ok=True)
        if not configuration.data_root.is_dir() or not os.access(configuration.data_root, os.W_OK):
            failures.append("数据目录不可写")
        elif shutil.disk_usage(configuration.data_root).free < configuration.min_free_bytes:
            failures.append("数据目录剩余空间低于配置门槛")
    except OSError:
        failures.append("数据目录不可创建或不可写")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        failures.append("应用容器不能以 root 用户运行")
    required_binaries = [
        "ffmpeg",
        "ffprobe",
        "pdftoppm",
        "pg_dump",
        "pg_restore",
        "fc-list",
    ]
    if require_document_renderer:
        required_binaries.append("soffice")
    missing_binaries = [name for name in required_binaries if shutil.which(name) is None]
    if missing_binaries:
        failures.append("缺少运行组件: " + ", ".join(missing_binaries))
    validated_fonts: tuple[str, ...] = ()
    try:
        validated_fonts = validate_required_fonts(configuration.required_fonts)
    except PreflightError as exc:
        failures.append(str(exc))
    if failures:
        raise PreflightError("；".join(failures))
    return {
        "https": True,
        "secure_cookie": True,
        "database": "postgresql",
        "binaries": {name: "available" for name in required_binaries},
        "data_root": "configured",
        "fonts": {font: "available" for font in validated_fonts},
    }


def validate_ai_environment(environment: Mapping[str, str] | None = None) -> None:
    """Validate independent AI capability wiring without exposing its values."""

    source = os.environ if environment is None else environment
    failures: list[str] = []
    if source.get("FIRE_INSPECT_ALLOW_FONT_SUBSTITUTION", "0") == "1":
        failures.append("生产环境禁止字体替代渲染（FIRE_INSPECT_ALLOW_FONT_SUBSTITUTION）")
    for capability in ("ASR", "LLM", "VISION", "EMBEDDING"):
        prefix = f"FIRE_INSPECT_{capability}"
        provider = source.get(f"{prefix}_PROVIDER", "disabled").strip().lower()
        if provider == "fixed":
            failures.append(f"{capability} 生产环境禁止 fixed provider")
            continue
        if provider not in {"disabled", "http"}:
            failures.append(f"{capability} provider 不受支持")
            continue
        if provider == "http":
            missing = [
                suffix
                for suffix in ("ENDPOINT", "MODEL", "API_KEY")
                if not source.get(f"{prefix}_{suffix}", "").strip()
            ]
            if missing:
                failures.append(f"{capability} http provider 配置不完整")
                continue
            endpoint = source[f"{prefix}_ENDPOINT"]
            parsed = urlparse(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                failures.append(f"{capability} endpoint 格式无效")
    if failures:
        raise PreflightError("；".join(failures))
