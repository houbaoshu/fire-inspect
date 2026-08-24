from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from fire_inspect.operations.restore import RestoreValidationError, verify_backup_bundle


class Command(BaseCommand):
    help = "Verify the exact backup bundle checksums, manifest and safe archive members."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)

    def handle(self, *args, **options):
        try:
            manifest = verify_backup_bundle(Path(options["source"]))
        except RestoreValidationError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"verified backup bundle entries: {len(manifest.entries)}")
