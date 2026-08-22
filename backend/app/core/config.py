"""应用配置：全部走环境变量（PLAN §1.2），禁止写死外部端点。"""
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # 仓库根目录


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=[ROOT / ".env", ROOT / "backend" / ".env"], extra="ignore")

    database_url: str = "postgresql+psycopg://fire:fire@localhost:5432/fire_inspect"
    data_dir: Path = ROOT / "data"
    static_dir: str = Field(default="", validation_alias="FI_STATIC_DIR")   # 前端产物目录（默认 ROOT/frontend/dist）
    seed_dir: str = Field(default="", validation_alias="FI_SEED_DIR")       # 种子数据目录（默认仓库 testdata/docs；容器内 /seed）
    session_secret: str = "dev-secret-change-me"
    session_ttl_hours: int = 72

    # 五组 AI 端点（均可独立配置；缺省回落到 DASHSCOPE_* 便于开发）
    dashscope_base_url: str = ""
    dashscope_api_key: str = ""

    asr_base_url: str = ""
    asr_api_key: str = ""
    asr_model: str = "qwen3-asr-flash"

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "qwen3.7-flash"

    vl_base_url: str = ""
    vl_api_key: str = ""
    vl_model: str = "qwen3-vl-flash"

    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "qwen3.7-text-embedding"
    embed_dim: int = 1024  # 已实测 qwen3.7-text-embedding = 1024

    ocr_base_url: str = ""
    ocr_api_key: str = ""
    ocr_model: str = "qwen3.5-ocr"

    # 媒体流水线参数（ASR-VERIFICATION §4：窗口分段，硬切边界留 1s 重叠）
    asr_window_sec: float = 10.0
    asr_overlap_sec: float = 1.0
    keyframe_interval_sec: float = 3.0
    keyframe_blur_threshold: float = 15.0   # 拉普拉斯方差（手机实拍视频量级）
    keyframe_dark_threshold: float = 35.0   # 平均亮度(0-255)
    keyframe_phash_hamming: int = 6         # 相似帧去重阈值
    keyframe_max: int = 30                  # 候选帧上限（澄清 §3：数量适中）
    keyframe_min_keep: int = 3              # 兜底：过滤后至少保留数

    def resolve(self, group: str) -> tuple[str, str, str]:
        """返回 (base_url, api_key, model)，组内缺省时回落 DASHSCOPE_*。"""
        base = getattr(self, f"{group}_base_url") or self.dashscope_base_url
        key = getattr(self, f"{group}_api_key") or self.dashscope_api_key
        model = getattr(self, f"{group}_model")
        return base.rstrip("/"), key, model

    def ai_available(self, group: str) -> bool:
        base, key, _ = self.resolve(group)
        return bool(base and key)


settings = Settings()

MEDIA_DIR = settings.data_dir / "media"
EXPORT_DIR = settings.data_dir / "exports"
for d in (settings.data_dir, MEDIA_DIR, EXPORT_DIR):
    d.mkdir(parents=True, exist_ok=True)
