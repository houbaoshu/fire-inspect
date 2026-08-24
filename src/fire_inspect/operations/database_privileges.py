from __future__ import annotations

from collections.abc import Iterable

from django.db import connection as default_connection
from django.db import transaction
from psycopg import sql

MIGRATION_ROLE = "fire_inspect_migrator"
RUNTIME_ROLE = "fire_inspect_runtime"
APPLICATION_SCHEMA = "public"
AUDIT_TABLE = "audit_auditevent"
AUDIT_TRIGGER = "audit_auditevent_append_only_guard"
AUDIT_TRUNCATE_TRIGGER = "audit_auditevent_truncate_guard"


class DatabasePrivilegeError(RuntimeError):
    pass


def privilege_statements(*, database_name: str = "fire_inspect") -> tuple[sql.Composed, ...]:
    """Build the identifier-safe deployment privilege plan.

    Runtime receives ordinary application CRUD and sequence use, while the
    permanent audit table is reduced to append/read only. PostgreSQL 17's
    MAINTAIN privilege is revoked explicitly as part of the exact audit grant.
    """

    database = sql.Identifier(database_name)
    schema = sql.Identifier(APPLICATION_SCHEMA)
    runtime = sql.Identifier(RUNTIME_ROLE)
    migrator = sql.Identifier(MIGRATION_ROLE)
    audit_table = sql.Identifier(APPLICATION_SCHEMA, AUDIT_TABLE)
    return (
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, runtime),
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(schema),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, runtime),
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM PUBLIC").format(schema),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
            schema, runtime
        ),
        sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM PUBLIC").format(schema),
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(schema, runtime),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON TABLES FROM PUBLIC"
        ).format(migrator, schema),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(migrator, schema, runtime),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE ALL ON SEQUENCES FROM PUBLIC"
        ).format(migrator, schema),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
            "GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(migrator, schema, runtime),
        sql.SQL("REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON {} FROM {}").format(
            audit_table, runtime
        ),
        sql.SQL("REVOKE MAINTAIN ON {} FROM {}").format(audit_table, runtime),
        sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(audit_table, runtime),
    )


def _migration_owner(
    database_connection,
) -> tuple[str, tuple[bool, bool, bool, bool, bool], str, str, str]:
    with database_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_user,
                   role_info.rolsuper,
                   role_info.rolcreatedb,
                   role_info.rolcreaterole,
                   role_info.rolreplication,
                   role_info.rolbypassrls,
                   current_database(),
                   pg_get_userbyid(database_info.datdba),
                   pg_get_userbyid(namespace_info.nspowner)
              FROM pg_roles AS role_info
              JOIN pg_database AS database_info
                ON database_info.datname = current_database()
              JOIN pg_namespace AS namespace_info
                ON namespace_info.nspname = %s
             WHERE role_info.rolname = current_user
            """,
            [APPLICATION_SCHEMA],
        )
        row = cursor.fetchone()
    if row is None:
        raise DatabasePrivilegeError("无法核验数据库迁移角色")
    role_attributes = tuple(bool(value) for value in row[1:6])
    return str(row[0]), role_attributes, str(row[6]), str(row[7]), str(row[8])


def configure_database_privileges(database_connection=None) -> Iterable[sql.Composed]:
    """Apply runtime grants while connected as the non-superuser owner."""

    connection = database_connection or default_connection
    if connection.vendor != "postgresql":
        raise DatabasePrivilegeError("数据库权限配置仅支持 PostgreSQL 17")
    current_role, role_attributes, database_name, database_owner, schema_owner = _migration_owner(
        connection
    )
    if current_role != MIGRATION_ROLE:
        raise DatabasePrivilegeError("数据库权限只能由 fire_inspect_migrator 配置")
    if role_attributes[0]:
        raise DatabasePrivilegeError("数据库迁移角色不能是超级用户")
    if any(role_attributes[1:]):
        raise DatabasePrivilegeError("数据库迁移角色不能拥有管理或绕过权限")
    if database_owner != MIGRATION_ROLE or schema_owner != MIGRATION_ROLE:
        raise DatabasePrivilegeError("数据库及 public schema 必须由迁移角色持有")

    statements = privilege_statements(database_name=database_name)
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    return statements
