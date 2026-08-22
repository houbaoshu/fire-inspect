import os
import shutil
from pathlib import Path

os.environ["FIRE_ENV"] = "test"
os.environ["FIRE_DATABASE_URL"] = "sqlite:///./.data/test.db"
os.environ["FIRE_STORAGE_BACKEND"] = "local"
os.environ["FIRE_LOCAL_STORAGE_PATH"] = ".data/test-objects"
os.environ["FIRE_TASK_ALWAYS_EAGER"] = "true"
os.environ["FIRE_AI_PROVIDER"] = "mock"

import pytest
from fastapi.testclient import TestClient

from backend.app.database import Base, SessionLocal, engine
from backend.app.main import app
from backend.app.models import User, UserRole
from backend.app.security import hash_password


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    storage = Path(".data/test-objects")
    if storage.exists():
        shutil.rmtree(storage)
    with SessionLocal() as db:
        db.add_all(
            [
                User(
                    username="admin",
                    display_name="管理员",
                    password_hash=hash_password("admin-password"),
                    role=UserRole.ADMIN,
                ),
                User(
                    username="inspector",
                    display_name="检查员",
                    password_hash=hash_password("inspect-password"),
                    role=UserRole.INSPECTOR,
                ),
                User(
                    username="outsider",
                    display_name="无关人员",
                    password_hash=hash_password("outside-password"),
                    role=UserRole.INSPECTOR,
                ),
            ]
        )
        db.commit()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("fire_csrf")}


@pytest.fixture
def inspector_headers(client: TestClient):
    return login(client, "inspector", "inspect-password")


@pytest.fixture
def admin_headers(client: TestClient):
    return login(client, "admin", "admin-password")
