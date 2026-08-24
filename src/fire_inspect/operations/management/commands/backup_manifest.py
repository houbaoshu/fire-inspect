from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.migrations.recorder import MigrationRecorder

from fire_inspect.operations.manifest import BackupManifest, build_manifest, verify_manifest

BACKUP_ROOTS = (
    "originals",
    "derived",
    "knowledge",
    "templates",
    "documents",
    "render-previews",
)


def _application_version() -> str:
    try:
        return version("fire-inspect")
    except PackageNotFoundError:
        return "0.1.0+source"


def _migration_revision() -> str:
    rows = MigrationRecorder.Migration.objects.order_by("app", "name").values_list("app", "name")
    return ",".join(f"{app}:{name}" for app, name in rows) or "unmigrated"


class Command(BaseCommand):
    help = "Create or verify the per-file manifest used by consistent backups."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("create", "verify"))
        parser.add_argument("--data-root", required=True)
        parser.add_argument("--output")
        parser.add_argument("--manifest")

    def handle(self, *args, **options):
        try:
            data_root = Path(options["data_root"]).resolve()
        except OSError as exc:
            raise CommandError("备份数据根目录不可访问。") from exc
        if data_root == Path("/") or data_root == Path.home().resolve():
            raise CommandError("拒绝对不安全的数据根目录执行备份操作。")
        if options["action"] == "create":
            if not options.get("output"):
                raise CommandError("create 需要 --output。")
            output = Path(options["output"])
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                manifest = build_manifest(
                    data_root,
                    BACKUP_ROOTS,
                    application_version=_application_version(),
                    migration_revision=_migration_revision(),
                )
                output.write_text(manifest.to_json(), encoding="utf-8")
            except (OSError, ValueError) as exc:
                raise CommandError("无法创建安全的备份逐文件清单。") from exc
            self.stdout.write(f"manifest entries: {len(manifest.entries)}")
            return
        if not options.get("manifest"):
            raise CommandError("verify 需要 --manifest。")
        try:
            manifest = BackupManifest.from_json(
                Path(options["manifest"]).read_text(encoding="utf-8")
            )
            failures = verify_manifest(data_root, manifest)
        except (OSError, UnicodeError, ValueError, KeyError) as exc:
            raise CommandError("备份逐文件清单无效或不可读。") from exc
        if failures:
            raise CommandError("备份文件校验失败：" + "；".join(failures[:10]))
        self.stdout.write(f"verified manifest entries: {len(manifest.entries)}")
