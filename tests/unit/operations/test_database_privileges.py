from __future__ import annotations

import pytest

from fire_inspect.operations.database_privileges import (
    DatabasePrivilegeError,
    configure_database_privileges,
    privilege_statements,
)


def test_runtime_privilege_plan_grants_crud_then_restricts_audit_mutation() -> None:
    rendered = "\n".join(statement.as_string() for statement in privilege_statements())

    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "public" '
        'TO "fire_inspect_runtime"' in rendered
    )
    assert (
        'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "public" '
        'TO "fire_inspect_runtime"' in rendered
    )
    assert (
        "REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON "
        '"public"."audit_auditevent" FROM "fire_inspect_runtime"' in rendered
    )
    assert (
        'GRANT SELECT, INSERT ON "public"."audit_auditevent" TO "fire_inspect_runtime"' in rendered
    )
    assert "GRANT ALL" not in rendered


@pytest.mark.parametrize(
    ("row", "message"),
    (
        (
            (
                "fire_inspect_bootstrap",
                True,
                False,
                False,
                False,
                False,
                "fire_inspect",
                "x",
                "x",
            ),
            "只能由",
        ),
        (
            (
                "fire_inspect_migrator",
                True,
                False,
                False,
                False,
                False,
                "fire_inspect",
                "fire_inspect_migrator",
                "fire_inspect_migrator",
            ),
            "不能是超级用户",
        ),
        (
            (
                "fire_inspect_migrator",
                False,
                False,
                False,
                False,
                False,
                "fire_inspect",
                "fire_inspect_bootstrap",
                "fire_inspect_migrator",
            ),
            "必须由迁移角色持有",
        ),
    ),
)
def test_privilege_configuration_requires_non_superuser_database_owner(row, message) -> None:
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement, params=None):
            return None

        def fetchone(self):
            return row

    class Connection:
        vendor = "postgresql"

        def cursor(self):
            return Cursor()

    with pytest.raises(DatabasePrivilegeError, match=message):
        tuple(configure_database_privileges(Connection()))
