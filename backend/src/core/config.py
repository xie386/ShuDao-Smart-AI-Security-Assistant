"""应用配置：pydantic-settings 读取 .env，全项目经 settings 单例访问。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用 ----
    APP_NAME: str = "蜀道智能安全助手"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    # ---- 安全（JWT）----
    SECRET_KEY: str = "dev-secret-key-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h，开发期宽松

    # ---- 数据层 ----
    DATABASE_URL: str = (
        "mysql+mysqlconnector://root:123456@127.0.0.1:3306/shudao_safety?charset=utf8mb4"
    )
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # ---- AI 模型（P2 线接入；D1 仅占位，避免硬编码散落代码）----
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"

    AGNES_API_KEY: str = ""
    AGNES_BASE_URL: str = "https://apihub.agnes-ai.com/v1"
    AGNES_MODEL: str = "agnes-2.5-flash"

    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = ""
    QWEN_MODEL: str = "qwen3.8max"

    # ---- 文件存储 ----
    UPLOAD_DIR: str = "uploads"

    # ---- CORS ----
    CORS_ORIGINS: str = "*"  # 逗号分隔；开发期默认全放开，上线由 P4 收紧


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
