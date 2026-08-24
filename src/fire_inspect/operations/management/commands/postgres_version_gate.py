from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from fire_inspect.operations.preflight import PreflightError, validate_postgres_runtime


class Command(BaseCommand):
    help = "Require PostgreSQL 17 server, pg_dump and pg_restore before maintenance work."

    def handle(self, *args, **options):
        try:
            result = validate_postgres_runtime()
        except PreflightError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"PostgreSQL runtime gate passed: {result['server']}.x")
