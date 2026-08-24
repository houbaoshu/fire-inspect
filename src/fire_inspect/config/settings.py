from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[3]
ENVIRONMENT = os.getenv("FIRE_INSPECT_ENV", "development").strip().lower()
DEBUG = os.getenv("FIRE_INSPECT_DEBUG", "1" if ENVIRONMENT != "production" else "0") == "1"


def _production_secret() -> str:
    value = os.getenv("FIRE_INSPECT_SECRET_KEY", "")
    if ENVIRONMENT == "production" and (len(value) < 50 or value.startswith("dev-")):
        raise ImproperlyConfigured("生产环境必须配置长度不少于 50 的随机密钥。")
    return value or "dev-only-unsafe-secret-key-never-use-in-production"


SECRET_KEY = _production_secret()
_allowed_hosts_raw = os.getenv("FIRE_INSPECT_ALLOWED_HOSTS", "")
if ENVIRONMENT == "production" and not _allowed_hosts_raw.strip():
    raise ImproperlyConfigured("生产环境必须配置明确的可信 Host。")
ALLOWED_HOSTS = [
    item.strip()
    for item in (_allowed_hosts_raw or "localhost,127.0.0.1,testserver").split(",")
    if item.strip()
]
if ENVIRONMENT == "production" and (not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured("生产环境必须配置明确的可信 Host。")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fire_inspect.ai.apps.AIConfig",
    "fire_inspect.accounts",
    "fire_inspect.audit",
    "fire_inspect.inspections",
    "fire_inspect.jobs",
    "fire_inspect.materials",
    "fire_inspect.knowledge.apps.KnowledgeConfig",
    "fire_inspect.templates.apps.TemplatesConfig",
    "fire_inspect.documents.apps.DocumentsConfig",
    "fire_inspect.media_pipeline.apps.MediaPipelineConfig",
    "fire_inspect.assistant.apps.AssistantConfig",
    "fire_inspect.operations.apps.OperationsConfig",
    "fire_inspect.transcripts.apps.TranscriptsConfig",
    "fire_inspect.facts.apps.FactsConfig",
    "fire_inspect.evidence.apps.EvidenceConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "fire_inspect.common.observability.CorrelationMiddleware",
    "fire_inspect.operations.middleware.MaintenanceWindowMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "fire_inspect.materials.middleware.UploadConcurrencyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fire_inspect.config.urls"
WSGI_APPLICATION = "fire_inspect.config.wsgi.application"
ASGI_APPLICATION = "fire_inspect.config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]


def _database_config() -> dict[str, object]:
    url = os.getenv("FIRE_INSPECT_DATABASE_URL", "").strip()
    if not url and ENVIRONMENT != "production":
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("FIRE_INSPECT_SQLITE_PATH", str(BASE_DIR / "data/dev.sqlite3")),
            "OPTIONS": {"timeout": 20},
        }
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ImproperlyConfigured("仅支持 PostgreSQL 数据库 URL。")
        try:
            port = parsed.port or 5432
        except ValueError as exc:
            raise ImproperlyConfigured("PostgreSQL 数据库端口无效。") from exc
        values = {
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": port,
        }
    else:
        # Compose passes libpq's discrete PG* variables instead of interpolating the
        # password into a URL.  This deliberately supports strong passwords containing
        # URL-reserved characters such as @, /, #, ? and % without an encoding trap.
        values = {
            "NAME": os.getenv("PGDATABASE", "").strip(),
            "USER": os.getenv("PGUSER", "").strip(),
            "PASSWORD": os.getenv("PGPASSWORD", ""),
            "HOST": os.getenv("PGHOST", "").strip(),
            "PORT": os.getenv("PGPORT", "5432").strip(),
        }
    if not all(values[key] for key in ("NAME", "USER", "PASSWORD", "HOST", "PORT")):
        raise ImproperlyConfigured("生产环境必须配置完整的 PostgreSQL 连接参数。")
    try:
        values["PORT"] = int(values["PORT"])
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("PostgreSQL 数据库端口无效。") from exc
    return {
        "ENGINE": "django.db.backends.postgresql",
        **values,
        "CONN_MAX_AGE": int(os.getenv("FIRE_INSPECT_DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }


DATABASES = {"default": _database_config()}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_ROOT = Path(os.getenv("FIRE_INSPECT_DATA_ROOT", str(BASE_DIR / "data")))
FIRE_INSPECT_STAGING_ROOT = Path(
    os.getenv("FIRE_INSPECT_STAGING_ROOT", str(MEDIA_ROOT / "staging"))
)
FIRE_INSPECT_QUARANTINE_ROOT = Path(
    os.getenv("FIRE_INSPECT_QUARANTINE_ROOT", str(MEDIA_ROOT / "quarantine/deletions"))
)
FIRE_INSPECT_MAINTENANCE_FILE = Path(
    os.getenv("FIRE_INSPECT_MAINTENANCE_FILE", str(MEDIA_ROOT / ".maintenance"))
)
FIRE_INSPECT_MIN_FREE_BYTES = int(os.getenv("FIRE_INSPECT_MIN_FREE_BYTES", 1024**3))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FIRE_INSPECT_MAX_UPLOAD_BYTES = int(os.getenv("FIRE_INSPECT_MAX_UPLOAD_BYTES", 512 * 1024**2))
FIRE_INSPECT_MAX_INSPECTION_BYTES = int(os.getenv("FIRE_INSPECT_MAX_INSPECTION_BYTES", 2 * 1024**3))
FIRE_INSPECT_MAX_MATERIALS_PER_INSPECTION = int(
    os.getenv("FIRE_INSPECT_MAX_MATERIALS_PER_INSPECTION", "50")
)
FIRE_INSPECT_MAX_MEDIA_DURATION_MS = int(
    os.getenv("FIRE_INSPECT_MAX_MEDIA_DURATION_MS", str(2 * 60 * 60 * 1000))
)
FIRE_INSPECT_MAX_CONCURRENT_UPLOADS = int(
    os.getenv("FIRE_INSPECT_MAX_CONCURRENT_UPLOADS", "4")
)
FIRE_INSPECT_JOB_LEASE_SECONDS = int(os.getenv("FIRE_INSPECT_JOB_LEASE_SECONDS", "120"))
FIRE_INSPECT_WORKER_CONCURRENCY = int(os.getenv("FIRE_INSPECT_WORKER_CONCURRENCY", "2"))

APPEND_SLASH = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = ENVIRONMENT == "production"
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = ENVIRONMENT == "production"
CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in os.getenv("FIRE_INSPECT_CSRF_TRUSTED_ORIGINS", "").split(",")
    if item.strip()
]
SECURE_SSL_REDIRECT = ENVIRONMENT == "production"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if ENVIRONMENT == "production" else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = ENVIRONMENT == "production"
SECURE_HSTS_PRELOAD = ENVIRONMENT == "production"
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "fire_inspect.common.observability.JsonLogFormatter",
            "sensitive_roots": [str(MEDIA_ROOT), str(FIRE_INSPECT_STAGING_ROOT)],
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"handlers": ["console"], "level": os.getenv("FIRE_INSPECT_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "fire_inspect": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "fire_inspect.common.authentication.UnauthorizedSessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "fire_inspect.common.api.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "multipart",
}

DATA_UPLOAD_MAX_MEMORY_SIZE = min(FIRE_INSPECT_MAX_UPLOAD_BYTES, 10 * 1024**2)
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024**2
