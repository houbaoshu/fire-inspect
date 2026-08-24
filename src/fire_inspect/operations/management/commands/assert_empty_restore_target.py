from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Fail unless the configured PostgreSQL restore target has no user tables."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("恢复目标必须是 PostgreSQL 数据库。")
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
                )
                table_count = int(cursor.fetchone()[0])
        except Exception as exc:
            raise CommandError("无法核验恢复目标数据库是否为空。") from exc
        if table_count:
            raise CommandError("恢复目标数据库必须为空；检测到已有业务表。")
        self.stdout.write("restore target database is empty")
