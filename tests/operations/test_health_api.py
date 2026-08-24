from pathlib import Path

import pytest


@pytest.mark.django_db
def test_health_endpoints_are_safe_and_do_not_require_login(api_client) -> None:
    live = api_client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "live"}
    ready = api_client.get("/health/ready")
    assert ready.status_code in {200, 503}
    assert set(ready.json()) == {"status", "checks"}
    serialized = str(ready.json())
    assert "/Users/" not in serialized
    assert "SECRET" not in serialized


@pytest.mark.django_db
def test_readiness_reports_maintenance_without_exposing_marker_path(
    api_client, settings, tmp_path: Path
) -> None:
    marker = tmp_path / "private-maintenance-marker"
    marker.write_text("", encoding="utf-8")
    settings.FIRE_INSPECT_MAINTENANCE_FILE = marker
    response = api_client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["maintenance"] == "unavailable"
    assert str(marker) not in str(response.json())
