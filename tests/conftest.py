from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from rest_framework.test import APIClient


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-local-acceptance", action="store_true", default=False)
    parser.addoption("--run-real-ai", action="store_true", default=False)
    parser.addoption("--run-windows-docx", action="store_true", default=False)
    parser.addoption("--run-postgres", action="store_true", default=False)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    switches = {
        "local_acceptance": "--run-local-acceptance",
        "real_ai": "--run-real-ai",
        "windows_docx": "--run-windows-docx",
        "postgres": "--run-postgres",
    }
    for marker, option in switches.items():
        if config.getoption(option):
            continue
        skip = pytest.mark.skip(reason=f"requires explicit {option}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated_media_root(settings, tmp_path: Path) -> None:
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.FIRE_INSPECT_STAGING_ROOT = tmp_path / "staging"
    settings.FIRE_INSPECT_QUARANTINE_ROOT = tmp_path / "quarantine"
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def user_factory(db, django_user_model) -> Callable[..., object]:
    def factory(
        username: str,
        *,
        password: str = "Strong-Test-Password-42!",
        role: str = "inspector",
        status: str = "active",
    ):
        return django_user_model.objects.create_user(
            username=username,
            password=password,
            role=role,
            status=status,
        )

    return factory


@pytest.fixture
def inspector_a(user_factory):
    return user_factory("inspector-a")


@pytest.fixture
def inspector_b(user_factory):
    return user_factory("inspector-b")


@pytest.fixture
def admin_user(user_factory):
    return user_factory("admin", role="admin")


@pytest.fixture
def authenticated_client(api_client: APIClient):
    def factory(user) -> APIClient:
        api_client.force_authenticate(user=user)
        return api_client

    return factory
