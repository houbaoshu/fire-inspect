from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from fire_inspect.operations.redaction import redact_text

_request_id: ContextVar[str] = ContextVar("fire_inspect_request_id", default="-")
_job_id: ContextVar[str] = ContextVar("fire_inspect_job_id", default="-")
_SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


def current_request_id() -> str:
    return _request_id.get()


def current_job_id() -> str:
    return _job_id.get()


def safe_correlation_id(candidate: object) -> str:
    value = str(candidate or "").strip()
    if _SAFE_CORRELATION.fullmatch(value):
        return value
    return uuid.uuid4().hex


@contextmanager
def correlation_context(
    *, request_id: str | None = None, job_id: str | None = None
) -> Iterator[None]:
    request_token = _request_id.set(safe_correlation_id(request_id)) if request_id else None
    job_token = _job_id.set(safe_correlation_id(job_id)) if job_id else None
    try:
        yield
    finally:
        if job_token is not None:
            _job_id.reset(job_token)
        if request_token is not None:
            _request_id.reset(request_token)


class CorrelationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = safe_correlation_id(request.headers.get("X-Request-ID"))
        with correlation_context(request_id=request_id):
            request.correlation_id = request_id
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            return response


class JsonLogFormatter(logging.Formatter):
    """Small, dependency-free JSON formatter with mandatory redaction."""

    def __init__(self, *, sensitive_roots=()):
        super().__init__()
        self.sensitive_roots = tuple(str(item) for item in sensitive_roots)

    def format(self, record: logging.LogRecord) -> str:
        message = redact_text(record.getMessage(), sensitive_roots=self.sensitive_roots)
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": message,
            "request_id": current_request_id(),
            "job_id": current_job_id(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
