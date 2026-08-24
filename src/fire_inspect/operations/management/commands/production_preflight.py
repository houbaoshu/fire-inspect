from __future__ import annotations

import os

from django.core.management.base import BaseCommand, CommandError

from fire_inspect.operations.preflight import (
    PreflightError,
    ProductionConfiguration,
    run_preflight,
    validate_ai_environment,
    validate_postgres_runtime,
)


class Command(BaseCommand):
    help = "Fail closed when required production configuration or runtime dependencies are unsafe."

    def handle(self, *args, **options):
        try:
            result = run_preflight(ProductionConfiguration.from_environment())
            validate_ai_environment()
            expected_role = os.environ.get("FIRE_INSPECT_DATABASE_ROLE", "").strip()
            if not expected_role:
                raise PreflightError("必须配置生产数据库职责角色")
            postgres = validate_postgres_runtime(expected_role=expected_role)
        except PreflightError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            self.style.SUCCESS(
                "production preflight passed: "
                + ", ".join(sorted(result["binaries"].keys()))
                + f"; PostgreSQL {postgres['server']}"
            )
        )
