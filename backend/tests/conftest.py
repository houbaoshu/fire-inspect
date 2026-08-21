import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="fire-inspect-tests-"))
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{TEST_ROOT / 'test.sqlite3'}",
        "STORAGE_BACKEND": "local",
        "STORAGE_ROOT": str(TEST_ROOT / "storage"),
        "TASK_MODE": "inline",
        "BOOTSTRAP_ADMIN_USERNAME": "admin",
        "BOOTSTRAP_ADMIN_PASSWORD": "test-password",
        "SAMPLE_TEMPLATES_DIR": "templates/sample",
        "AI_BASE_URL": "",
        "AI_API_KEY": "",
    }
)

import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, engine
from app.core.storage import reset_storage
from app.main import app, seed_data


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    reset_storage()
    seed_data()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str = "admin", password: str = "test-password"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()
