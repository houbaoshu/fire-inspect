from __future__ import annotations

import os
import shutil
from pathlib import Path

from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class LiveView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"status": "live"})


class ReadyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        checks: dict[str, str] = {}
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            checks["database"] = "ready"
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SHOW server_version_num")
                    server_major = int(str(cursor.fetchone()[0])) // 10000
                checks["postgresql_17"] = "ready" if server_major == 17 else "unavailable"
            else:
                checks["postgresql_17"] = "unavailable"
        except Exception:
            checks["database"] = "unavailable"
            checks["postgresql_17"] = "unavailable"
        data_root = Path(settings.MEDIA_ROOT)
        try:
            data_root.mkdir(parents=True, exist_ok=True)
            checks["storage"] = (
                "ready" if data_root.is_dir() and os.access(data_root, os.W_OK) else "unavailable"
            )
        except OSError:
            checks["storage"] = "unavailable"
        try:
            free_bytes = shutil.disk_usage(data_root).free
            checks["disk"] = (
                "ready" if free_bytes >= settings.FIRE_INSPECT_MIN_FREE_BYTES else "unavailable"
            )
        except OSError:
            checks["disk"] = "unavailable"
        try:
            checks["maintenance"] = (
                "unavailable" if settings.FIRE_INSPECT_MAINTENANCE_FILE.exists() else "ready"
            )
        except OSError:
            checks["maintenance"] = "unavailable"
        for binary in ("ffmpeg", "ffprobe", "soffice", "pdftoppm", "pg_dump", "pg_restore"):
            checks[binary] = "ready" if shutil.which(binary) else "unavailable"
        ready = all(value == "ready" for value in checks.values())
        return Response(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )
