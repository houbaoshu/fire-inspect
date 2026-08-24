from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

ROOT = Path(__file__).resolve().parents[2]


def test_test_runtime_uses_sqlite_and_production_driver_is_available() -> None:
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    import psycopg

    assert psycopg.__version__


def test_production_refuses_to_start_without_explicit_security_configuration() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FIRE_INSPECT_") and key != "DJANGO_SETTINGS_MODULE"
    }
    environment["FIRE_INSPECT_ENV"] = "production"
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "try:\n import fire_inspect.config.settings\n"
                "except Exception as exc:\n"
                " print(type(exc).__name__ + ':' + str(exc))\n"
                " raise SystemExit(2)"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "SECRET" not in combined
    assert "/Users/" not in combined


def test_no_runtime_dependency_requires_a_dotenv_file() -> None:
    settings_source = (ROOT / "src/fire_inspect/config/settings.py").read_text(encoding="utf-8")
    assert "dotenv" not in settings_source.casefold()


def test_frontend_csp_keeps_vite_styles_local_and_renderable() -> None:
    """Vite injects the local CSS module as a style element in development."""

    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "style-src 'self' 'unsafe-inline'" in html
    assert "script-src 'self'" in html
    assert "https:" not in html and "http:" not in html


def test_production_database_password_supports_url_reserved_characters_without_a_url() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("FIRE_INSPECT_")
        and not key.startswith("PG")
        and key != "DJANGO_SETTINGS_MODULE"
    }
    password = "Strong@:/?#%+Database-Password-42!"
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "FIRE_INSPECT_ENV": "production",
            "FIRE_INSPECT_SECRET_KEY": "Production-Secret-9fA2!" * 3,
            "FIRE_INSPECT_ALLOWED_HOSTS": "fire-inspect.test",
            "PGHOST": "db",
            "PGPORT": "5432",
            "PGDATABASE": "fire_inspect",
            "PGUSER": "fire_inspect",
            "PGPASSWORD": password,
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from fire_inspect.config import settings; "
                f"assert settings.DATABASES['default']['PASSWORD'] == {password!r}; "
                "assert settings.DATABASES['default']['HOST'] == 'db'"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert password not in result.stdout + result.stderr
