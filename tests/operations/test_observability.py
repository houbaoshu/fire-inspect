from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from fire_inspect.common.observability import (
    JsonLogFormatter,
    correlation_context,
    current_job_id,
    current_request_id,
)
from fire_inspect.operations.middleware import maintenance_mode_enabled


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        "fire_inspect.test",
        logging.INFO,
        __file__,
        10,
        message,
        args,
        None,
    )


def test_json_log_formatter_includes_correlations_and_redacts_canaries() -> None:
    formatter = JsonLogFormatter(sensitive_roots=("/srv/fire-inspect/data",))
    with correlation_context(request_id="request-42", job_id="job-73"):
        payload = json.loads(
            formatter.format(
                _record(
                    "api_key=%s sessionid=COOKIE-CANARY /srv/fire-inspect/data/original",
                    "KEY-CANARY",
                )
            )
        )
    assert payload["request_id"] == "request-42"
    assert payload["job_id"] == "job-73"
    serialized = json.dumps(payload)
    assert "KEY-CANARY" not in serialized
    assert "COOKIE-CANARY" not in serialized
    assert "/srv/fire-inspect/data" not in serialized


def test_correlation_context_is_reset_after_scope() -> None:
    assert current_request_id() == "-"
    assert current_job_id() == "-"
    with correlation_context(request_id="request", job_id="job"):
        assert current_request_id() == "request"
        assert current_job_id() == "job"
    assert current_request_id() == "-"
    assert current_job_id() == "-"


@pytest.mark.django_db
def test_request_correlation_header_is_sanitized(api_client) -> None:
    response = api_client.get("/health/live", HTTP_X_REQUEST_ID="../../secret\nheader")
    assert response.status_code == 200
    correlation_id = response["X-Request-ID"]
    assert correlation_id
    assert ".." not in correlation_id
    assert "\n" not in correlation_id


@pytest.mark.django_db
def test_maintenance_window_blocks_writes_but_not_health(
    api_client, settings, tmp_path: Path
) -> None:
    marker = tmp_path / ".maintenance"
    settings.FIRE_INSPECT_MAINTENANCE_FILE = marker
    marker.touch()
    assert maintenance_mode_enabled()

    blocked = api_client.post("/api/v1/auth/login", {"username": "x", "password": "y"})
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "maintenance"
    assert api_client.get("/health/live").status_code == 200


def test_maintenance_check_fails_closed_for_directory_marker(settings, tmp_path: Path) -> None:
    marker = tmp_path / ".maintenance"
    marker.mkdir()
    settings.FIRE_INSPECT_MAINTENANCE_FILE = marker
    assert maintenance_mode_enabled()
