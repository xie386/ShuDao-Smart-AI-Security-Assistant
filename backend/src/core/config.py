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
    # 启动日志里展示的前端入口基址（部署到别的机器/端口时改这里）
    FRONT_BASE_URL: str = "http://127.0.0.1:8000"
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
    # v2.0 大点 05：内置 Redis（tools/redis/，裁定 1–8）
    REDIS_AUTOSTART: bool = True            # 探测不到时是否拉起内置实例
    REDIS_BIN_DIR: str = "tools/redis"      # 内置可执行文件目录（相对 team/，也可绝对路径）
    REDIS_BOOT_TIMEOUT: float = 5.0         # 拉起后等待 PING 的秒数

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

    # ---- PPT 工坊（v2.0 大点 02：百度 PPT LLM 已整体移除，改为本地 PPT 生成 Agent）----
    # 可选直连 PPT LLM（方式 A）：只留抽象位，当前无实现；配好 provider 才会启用
    PPT_LLM_PROVIDER: str = ""             # 预留：以后接别的付费 PPT LLM 时填实现名
    PPT_LLM_BASE_URL: str = ""
    PPT_LLM_API_KEY: str = ""

    # 默认链路（方式 B）：PPT 生成 Agent
    PPT_AGENT_ENABLED: bool = True
    PPT_AGENT_PAGE_LIMIT: int = 40           # 页数硬上限
    PPT_AGENT_MAX_FIX_PER_PAGE: int = 2      # 每页自检修补轮数上限
    PPT_AGENT_TOTAL_TIMEOUT_S: int = 900     # 整单超时（超时置失败，保留已产页）
    PPT_SANDBOX_ROOT: str = "user_data/ppt"  # 账号沙箱根（相对 backend/）
    PPT_PROGRESS_TTL: int = 7200             # Redis 进度 key TTL（秒）
    PPT_IMAGES_ENABLED: bool = True          # Openverse 配图开关（关掉只出图标/形状版式）
    PPT_IMAGE_API_BASE: str = "https://api.openverse.org/v1/images/"  # 可换镜像/代理地址
    PPT_IMAGE_TIMEOUT: float = 30.0          # 单次图片检索/下载超时
    PPT_IMAGE_MAX_PER_PAGE: int = 2          # 每页图片上限
    PPT_IMAGE_MAX_BYTES: int = 8 * 1024 * 1024   # 单图体积上限
    PPT_IMAGE_MAX_TOTAL: int = 20            # 单任务下载图片总数上限
    # 留空 = 自动定位：项目内 tools/officecli/officecli.exe → 项目根 officecli.exe → 本机旧安装路径
    PPT_OFFICECLI_PATH: str = ""
    PPT_OFFICECLI_TIMEOUT: float = 120.0     # 单次 officecli 调用超时

    # 多模态段（PPT Agent 专用，不接主模型）：默认 AGNES；PROVIDER 留多实现位
    PPT_VISION_PROVIDER: str = "agnes"
    PPT_VISION_BASE_URL: str = "https://apihub.agnes-ai.com/v1"
    PPT_VISION_API_KEY: str = ""
    PPT_VISION_MODEL: str = "agnes-2.5-flash"
    PPT_VISION_MAX_TOKENS: int = 800         # ★ 实测：它先输出 reasoning_content，预算小了 content 为空
    PPT_VISION_TIMEOUT: float = 60.0
    PPT_VISION_REVIEW: bool = True           # 逐页审图开关（关掉只截图不判定）

    # ---- 图库（v2.0 大点 02：Openverse 不可达时换 Unsplash；provider 可配）----
    UNSPLASH_APP_ID: str = ""
    UNSPLASH_ACCESS_KEY: str = ""            # Client-ID（search 接口用这个）
    UNSPLASH_SECRET_KEY: str = ""            # OAuth 用；search 不需要
    PPT_IMAGE_PROVIDER: str = "unsplash"     # unsplash（默认）/ openverse

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

    # ---- v2.0 大点 01：回答提速（重排精度 / 会话缓存 / 上下文预算 / Agent）----
    RERANKER_DTYPE: str = "fp16"          # fp16 / fp32；fp16 实测 n=30 由 36s 降到 3.1s 且排序不变
    RERANKER_BATCH_SIZE: int = 8          # 显式 batch，避免默认值放大显存峰值
    RAG_HIGH_PRECISION_DEFAULT: bool = False  # 请求未带 high_precision 时的默认（False=半精度=快）
    RAG_CONV_CACHE_ENABLED: bool = True   # 会话级子块缓存总开关
    RAG_CONV_CACHE_TTL: int = 600         # 滑动 TTL：回答完成后再计时，10 分钟无追问即清
    RAG_CONTEXT_BUDGET_CHARS: int = 16000  # 喂给模型的 context 字符预算（改造前 100K~209K）
    AGENT_ENABLED: bool = True            # Agent 化总开关；False → 直连 llm_client.chat_stream
    AGENT_MAX_TOOL_ROUNDS: int = 2        # Agent 最多工具轮数（含一次补检）
    AGENT_MAX_SPLIT_QUERIES: int = 2      # 多主题拆分上限（拆分=多次检索=时长翻倍）
    RAG_WARMUP_ON_START: bool = True      # 服务启动时后台预热检索链路（BM25/embedding/重排）

    # ---- v2.0 大点 03：意图识别（回答前门禁）----
    CHAT_INTENT_ENABLED: bool = True       # False = 完全跳过判定（回到 01 收官行为，应急回滚开关）
    CHAT_INTENT_TIMEOUT: float = 8.0       # 单次判定超时（秒）；超时按 knowledge 继续
    CHAT_INTENT_MAX_HISTORY_CHARS: int = 600   # 判定输入里「上一轮」文本上限
    CHAT_INTENT_MAX_TOKENS: int = 80       # 判定输出上限（一行 JSON 足够）

    # ---- v2.0 大点 04：知识贡献升级 + Agent 贡献渠道 ----
    KB_CONTRIB_MIN_CHARS: int = 1000       # 审核通过前的最小有效字数（清洗后）
    KB_BUILD_MIN_BYTES: int = 2000         # 全量重建时跳过的文件字节阈值（原硬编码 2000）
    KB_CONTRIB_EXTS: str = ".md,.markdown,.txt,.docx,.pdf,.html,.htm"
    KB_CONTRIB_MAX_MB: int = 10            # 单文件上限
    KB_MINERU_ENABLED: bool = True         # 关掉 → 全部走内置兜底转换
    KB_MINERU_CMD: str = "mineru-kit"      # 独立环境安装（uv tool install mineru）；kit 版免本地服务
    KB_MINERU_TIMEOUT: float = 600.0       # 单文件转换超时（39 页报告实测 89s）
    KB_MINERU_TIER: str = "basic"          # pdf: flash/basic/standard/advanced；docx 固定 flash
    KB_MINERU_ARGS: str = ""               # 额外 CLI 参数模板，可用 {input} {outdir}
    KB_AGENT_DOCS_DIR: str = "Methodology" # 相对项目根（team/）的方法论目录
    KB_AGENT_SCOUT_ENABLED: bool = True    # Agent 网搜总开关（回滚用）
    KB_FETCH_MAX_SITES: int = 3            # 单任务最多站点数
    KB_FETCH_MAX_PAGES: int = 8            # 单任务最多页面数
    KB_FETCH_MAX_BYTES: int = 8388608      # 单任务下载上限（8MB）
    KB_FETCH_TIMEOUT_S: int = 600          # 单任务总时长
    KB_FETCH_DENY_HOSTS: str = ""          # 逗号分隔的额外黑名单（内网/本机默认硬禁）
    # 采集搜索引擎（配置驱动，格式：`名称|搜索URL模板|结果选择器`，用 `;` 分隔，按顺序回退）
    # 百度对无头浏览器会弹验证码，故默认不含百度
    KB_SCOUT_ENGINES: str = (
        "360|https://www.so.com/s?q={query}|li.res-list h3 a, #main h3 a;"
        "sogou|https://www.sogou.com/web?query={query}|h3 a, .vrwrap h3 a;"
        "bing|https://cn.bing.com/search?q={query}&setlang=zh-CN&cc=CN|li.b_algo h3 a, h2 a"
    )
    KB_GAP_MIN_INSUFFICIENT: int = 1       # 触发采集的"资料不足"次数阈值（预留）

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
