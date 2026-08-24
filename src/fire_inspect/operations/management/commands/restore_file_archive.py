from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from fire_inspect.operations.restore import RestoreValidationError, restore_file_archive


class Command(BaseCommand):
    help = "Safely extract a verified application file archive into an empty data root."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--data-root", required=True)

    def handle(self, *args, **options):
        try:
            manifest = restore_file_archive(
                source=Path(options["source"]),
                destination=Path(options["data_root"]),
            )
        except RestoreValidationError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(f"restored file entries: {len(manifest.entries)}")
