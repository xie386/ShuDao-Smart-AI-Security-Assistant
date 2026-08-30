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
    # 本机 Redis 在 6380 且开启密码认证（requirepass=123456）
    REDIS_URL: str = "redis://:123456@127.0.0.1:6380/0"

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

    # ---- PPT 专职模型（D4 P05 主方案：百度云 PPT 模型，每日 10 次免费）----
    # 占位配置：D4 由用户提供真实 endpoint/key 后填写；免费次数用完后自动降级自研 gen_ppt.py 兜底
    BAIDU_PPT_API_KEY: str = ""
    BAIDU_PPT_BASE_URL: str = ""  # 百度云千帆 PPT 生成服务地址（D4 待填）
    BAIDU_PPT_MODEL: str = ""     # PPT 专职模型名（D4 待填）
    BAIDU_PPT_DAILY_QUOTA: int = 10  # 每日免费次数（用于配额判断，用尽走自研兜底）

    # ---- 文件存储 ----
    UPLOAD_DIR: str = "uploads"

    # ---- 文件上传（H01/H05，ARCHITECTURE §6 白名单+大小限制）----
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # ≤10MB
    ALLOWED_IMAGE_EXTENSIONS: str = "jpg,jpeg,png,webp"  # 逗号分隔

    # ---- 本地模型 / 向量库（P2 线）----
    EMBEDDING_MODEL_PATH: str = "D:/LLM/model/bge-large-zh"
    RERANKER_MODEL_PATH: str = "D:/LLM/model/bge-reranker-v2-m3"
    EMBEDDING_DEVICE: str = "cuda"  # cuda / cpu（用户决策：本机 RTX 3050 默认走 GPU；无 GPU 环境改 cpu）
    RERANKER_DEVICE: str = "cuda"
    # bge 官方建议：查询前加指令前缀，提升检索相关性
    EMBEDDING_QUERY_PREFIX: str = "为这个句子生成表示以用于检索相关文章："
    CHROMA_DIR: str = "chroma"  # Chroma 持久化目录（相对 backend/，.gitignore 已排除）
    CHROMA_COLLECTION: str = "safety_kb"
    RAG_TOP_K_RERANK: int = 4  # 重排后取前 N 喂大模型
    RAG_QUERY_REWRITE: bool = True  # §5.4 查询改写：口语问题 LLM 扩写多路子查询（增加一次 LLM 调用）
    RAG_MMR_LAMBDA: float = 0.7  # v3.0 MMR 相关性/多样性平衡杆：越大越保相关性（精确率），越小越保多样性
    RAG_MMR_DOC_CAP: int = 2  # v3.0 同文档最多入选席位数（文档级配额，治"同主题不同文档互相挤占"）
    RAG_TOPIC_BOOST: float = 0.3  # v4.0 主题一致性加权系数（匹配 query 主题的 rerank 分乘 1+α，治 plans 同质化）
    KNOWLEDGE_SRC_DIR: str = "knowledge_src"  # 知识库源文档目录（四类子目录）

    # ---- LLM 调用参数 ----
    LLM_TIMEOUT: float = 60.0
    LLM_MAX_RETRIES: int = 1
    LLM_TEMPERATURE: float = 0.7

    # ---- CORS ----
    CORS_ORIGINS: str = "*"  # 逗号分隔；开发期默认全放开，上线由 P4 收紧


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
