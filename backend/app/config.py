from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIRE_", env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite:///./.data/fire_inspect.db"
    storage_backend: str = "local"
    local_storage_path: Path = Path(".data/objects")
    redis_url: str = "redis://localhost:6379/0"
    task_always_eager: bool = True
    ai_provider: str = "mock"
    session_cookie: str = "fire_session"
    csrf_cookie: str = "fire_csrf"
    session_secure: bool = False
    session_hours: int = 12
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    max_upload_bytes: int = 5 * 1024 * 1024 * 1024

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "fireinspect"
    s3_secret_key: str = "change-me"
    s3_bucket: str = "fire-inspect"
    s3_region: str = "us-east-1"

    openai_base_url: str | None = None
    openai_api_key: str | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
    vision_model: str | None = None
    transcription_model: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
