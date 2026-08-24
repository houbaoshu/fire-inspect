from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from fire_inspect.operations.database_privileges import (
    DatabasePrivilegeError,
    configure_database_privileges,
)


class Command(BaseCommand):
    help = "Grant least-privilege application access after migrations."

    def handle(self, *args, **options):
        try:
            statements = tuple(configure_database_privileges())
        except DatabasePrivilegeError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(
            self.style.SUCCESS(
                f"database runtime privileges configured ({len(statements)} statements)"
            )
        )
