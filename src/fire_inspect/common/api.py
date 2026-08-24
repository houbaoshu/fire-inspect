from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from fire_inspect.operations.redaction import redact_text


class ApiError(APIException):
    """Stable, user-safe API error used by domain command endpoints."""

    status_code = 400
    default_code = "invalid_request"
    default_detail = "请求无法处理。"

    def __init__(self, *, code: str, message: str, status_code: int = 400) -> None:
        self.status_code = status_code
        self.error_code = code
        self.error_message = message
        super().__init__(detail=message, code=code)


def expected_revision(request, *body_keys: str) -> int:
    """Read an optimistic-lock revision from JSON or a standards-shaped If-Match."""

    keys = body_keys or ("revision", "expected_revision")
    raw = None
    for key in keys:
        if key in request.data:
            raw = request.data[key]
            break
    if raw is None:
        candidate = request.headers.get("If-Match", "").strip()
        match = re.fullmatch(r'(?:W/)?"?(\d+)"?', candidate)
        raw = match.group(1) if match else None
    if raw is None:
        raise ApiError(
            code="revision_required",
            message="修改时必须提交当前修订号。",
            status_code=428,
        )
    if isinstance(raw, bool):
        raise ApiError(code="invalid_revision", message="修订号格式不正确。")
    if not isinstance(raw, (int, str)) or (
        isinstance(raw, str) and re.fullmatch(r"\d+", raw.strip()) is None
    ):
        raise ApiError(code="invalid_revision", message="修订号格式不正确。")
    try:
        revision = int(raw.strip()) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise ApiError(code="invalid_revision", message="修订号格式不正确。") from exc
    if revision < 1:
        raise ApiError(code="invalid_revision", message="修订号格式不正确。")
    return revision


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    roots = (
        str(getattr(settings, "BASE_DIR", "")),
        str(getattr(settings, "MEDIA_ROOT", "")),
        str(getattr(settings, "FIRE_INSPECT_STAGING_ROOT", "")),
        str(getattr(settings, "FIRE_INSPECT_QUARANTINE_ROOT", "")),
    )
    return redact_text(value, sensitive_roots=roots)


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    response = drf_exception_handler(exc, context)
    if response is None:
        return Response(
            {
                "error": {
                    "code": "internal_error",
                    "message": "服务暂时无法处理请求，请稍后重试。",
                }
            },
            status=500,
        )

    if isinstance(exc, ApiError):
        response.data = {"error": {"code": exc.error_code, "message": _plain(exc.error_message)}}
        return response

    code = "request_error"
    message = "请求无法处理。"
    details: Any | None = None
    if hasattr(exc, "get_codes"):
        codes = exc.get_codes()
        if isinstance(codes, str):
            code = codes
        elif isinstance(codes, dict):
            code = "validation_error"
            details = _plain(response.data)
    if response.status_code == 401:
        code, message = "not_authenticated", "请先登录。"
    elif response.status_code == 403:
        code, message = "permission_denied", "没有执行此操作的权限。"
    elif response.status_code == 404:
        code, message = "not_found", "请求的资源不存在。"
    elif response.status_code == 405:
        code, message = "method_not_allowed", "不支持此操作。"
    elif details is None and isinstance(response.data, dict):
        details = _plain(response.data)

    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    response.data = payload
    return response
