from __future__ import annotations

import fcntl
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.http import JsonResponse

_UPLOAD_PATH = re.compile(r"^/api/v1/inspections/[0-9a-fA-F-]{36}/materials/?$")


class UploadCapacityExhausted(RuntimeError):
    pass


@dataclass(slots=True)
class UploadSlot:
    stream: BinaryIO

    def release(self) -> None:
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


def acquire_upload_slot(*, root: Path, capacity: int) -> UploadSlot:
    if capacity < 1:
        raise UploadCapacityExhausted
    root.mkdir(parents=True, exist_ok=True)
    for index in range(capacity):
        stream = (root / f"slot-{index}.lock").open("a+b")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            continue
        return UploadSlot(stream)
    raise UploadCapacityExhausted


class UploadConcurrencyMiddleware:
    """Reject excess uploads before DRF parses or stages their request bodies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.method != "POST"
            or _UPLOAD_PATH.fullmatch(request.path) is None
            or not getattr(request.user, "is_authenticated", False)
        ):
            return self.get_response(request)
        try:
            slot = acquire_upload_slot(
                root=Path(settings.FIRE_INSPECT_STAGING_ROOT) / ".upload-slots",
                capacity=settings.FIRE_INSPECT_MAX_CONCURRENT_UPLOADS,
            )
        except (OSError, UploadCapacityExhausted):
            response = JsonResponse(
                {
                    "error": {
                        "code": "upload_concurrency_limit",
                        "message": "当前上传任务已达并发上限，请稍后重试。",
                    }
                },
                status=429,
            )
            response["Retry-After"] = "1"
            return response
        try:
            return self.get_response(request)
        finally:
            slot.release()
