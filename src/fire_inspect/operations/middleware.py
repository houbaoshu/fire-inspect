from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def maintenance_mode_enabled() -> bool:
    try:
        return settings.FIRE_INSPECT_MAINTENANCE_FILE.exists()
    except OSError:
        # An unreadable marker location is not a safe condition for accepting writes.
        return True


class MaintenanceWindowMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.method not in _SAFE_METHODS
            and request.path.startswith("/api/")
            and maintenance_mode_enabled()
        ):
            return JsonResponse(
                {
                    "error": {
                        "code": "maintenance",
                        "message": "系统正在执行一致性维护，暂不接受写入，请稍后重试。",
                    }
                },
                status=503,
            )
        return self.get_response(request)
