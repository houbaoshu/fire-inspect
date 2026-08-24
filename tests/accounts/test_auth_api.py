from __future__ import annotations

import pytest
from django.contrib.auth import SESSION_KEY
from django.core.cache import cache
from rest_framework import status

from fire_inspect.audit.models import AuditEvent

pytestmark = pytest.mark.django_db


def test_business_api_requires_authentication_with_401(api_client) -> None:
    response = api_client.get("/api/v1/auth/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"]["code"] == "not_authenticated"


def test_login_current_user_and_logout(api_client, inspector_a, settings) -> None:
    settings.SESSION_COOKIE_SECURE = True
    response = api_client.post(
        "/api/v1/auth/login",
        {"username": " INSPECTOR-A ", "password": "Strong-Test-Password-42!"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "user": {
            "id": str(inspector_a.id),
            "username": "inspector-a",
            "role": "inspector",
            "status": "active",
        }
    }
    cookie = response.cookies[settings.SESSION_COOKIE_NAME]
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "Lax"
    assert SESSION_KEY in api_client.session
    assert AuditEvent.objects.filter(
        object_type="authentication", object_id=inspector_a.id, action="login_succeeded"
    ).exists()

    current = api_client.get("/api/v1/auth/me")
    assert current.status_code == status.HTTP_200_OK
    assert current.json()["user"]["username"] == "inspector-a"

    logged_out = api_client.post("/api/v1/auth/logout")
    assert logged_out.status_code == status.HTTP_204_NO_CONTENT
    assert AuditEvent.objects.filter(
        object_type="authentication", object_id=inspector_a.id, action="logout"
    ).exists()
    assert api_client.get("/api/v1/auth/me").status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize("password", ["wrong-password", "", None])
def test_login_rejects_invalid_credentials_without_detail(
    api_client, inspector_a, password
) -> None:
    response = api_client.post(
        "/api/v1/auth/login",
        {"username": inspector_a.username, "password": password},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"] == {
        "code": "invalid_credentials",
        "message": "用户名或密码错误。",
    }
    assert AuditEvent.objects.filter(object_type="authentication", action="login_failed").exists()


def test_login_rate_limit_resists_account_targeting(api_client) -> None:
    cache.clear()
    for _ in range(10):
        failed = api_client.post(
            "/api/v1/auth/login",
            {"username": "target-account", "password": "wrong-password"},
            format="json",
        )
        assert failed.status_code == status.HTTP_401_UNAUTHORIZED
    limited = api_client.post(
        "/api/v1/auth/login",
        {"username": "target-account", "password": "wrong-password"},
        format="json",
    )
    assert limited.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert limited.json()["error"]["code"] == "login_rate_limited"
    assert AuditEvent.objects.filter(action="login_failed").count() == 10
    assert AuditEvent.objects.filter(action="login_rate_limited").count() == 1


def test_disabled_user_cannot_login(api_client, user_factory) -> None:
    user_factory("disabled", status="disabled")
    response = api_client.post(
        "/api/v1/auth/login",
        {"username": "disabled", "password": "Strong-Test-Password-42!"},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_admin_user_management_is_forbidden_to_inspector(authenticated_client, inspector_a) -> None:
    response = authenticated_client(inspector_a).get("/api/v1/users")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_admin_can_create_and_disable_normalized_user(
    authenticated_client, admin_user, django_user_model
) -> None:
    client = authenticated_client(admin_user)
    created = client.post(
        "/api/v1/users",
        {
            "username": "  New.Inspector  ",
            "password": "Another-Strong-Password-42!",
            "role": "inspector",
        },
        format="json",
    )
    assert created.status_code == status.HTTP_201_CREATED
    assert created.json()["username"] == "new.inspector"
    user = django_user_model.objects.get(username="new.inspector")
    assert user.check_password("Another-Strong-Password-42!")

    disabled = client.patch(f"/api/v1/users/{user.id}", {"status": "disabled"}, format="json")
    assert disabled.status_code == status.HTTP_200_OK
    user.refresh_from_db()
    assert user.status == "disabled"
    assert user.is_active is False
    assert list(
        AuditEvent.objects.filter(object_type="user", object_id=user.id).values_list(
            "action", flat=True
        )
    ) == ["created", "updated"]
    assert "Another-Strong-Password-42!" not in "".join(
        AuditEvent.objects.filter(object_type="user", object_id=user.id).values_list(
            "masked_change_summary", flat=True
        )
    )
