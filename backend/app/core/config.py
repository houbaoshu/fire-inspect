from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "消防监督检查 AI 辅助系统"
    app_env: str = "development"
    api_prefix: str = "/api"
    frontend_origin: str = "http://localhost:5173"

    database_url: str = "sqlite:///./data/fire-inspect.sqlite3"
    redis_url: str = "redis://localhost:6379/0"
    task_mode: str = "inline"

    storage_backend: str = "local"
    storage_root: Path = Path("data/storage")
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "fire-inspect"
    s3_region: str = "us-east-1"

    session_cookie_name: str = "fire_inspect_session"
    session_cookie_secure: bool = False
    session_lifetime_hours: int = 12
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "change-me-now"

    max_upload_mb: int = 1024
    sample_templates_dir: Path = Path("templates/sample")

    ai_base_url: str | None = None
    ai_chat_base_url: str | None = None
    ai_transcription_base_url: str | None = None
    ai_embedding_base_url: str | None = None
    ai_api_key: str | None = None
    ai_chat_model: str | None = None
    ai_transcription_model: str | None = None
    ai_embedding_model: str | None = None
    ai_embedding_dimensions: int = Field(default=384, ge=32, le=4096)

    @property
    def chat_base_url(self) -> str | None:
        return self.ai_chat_base_url or self.ai_base_url

    @property
    def transcription_base_url(self) -> str | None:
        return self.ai_transcription_base_url or self.ai_base_url

    @property
    def embedding_base_url(self) -> str | None:
        return self.ai_embedding_base_url or self.ai_base_url

    @property
    def ai_enabled(self) -> bool:
        return bool(self.chat_base_url and self.ai_api_key and self.ai_chat_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
