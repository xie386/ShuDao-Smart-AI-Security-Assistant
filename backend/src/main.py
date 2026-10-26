"""蜀道智能安全助手 — 后端入口（应用工厂所在模块）。

工作原理（FastAPI 启动三要素）：
- 启动命令 `uvicorn src.main:app --reload --port 8000` 中：
    - `src.main` = 模块路径（即本文件 src/main.py）；
    - `app` = 本模块里定义的 FastAPI 实例变量名。
  uvicorn 会先完整执行本文件（import 即执行），再取出 `app` 作为 ASGI 应用托管。
- 本文件在 import 阶段从上到下依次完成：创建应用实例 → 装 CORS 中间件 →
  挂载子路由(include_router) → 挂载静态目录(mount) → 注册 /health 路由。

日常启动：
    cd backend && uvicorn src.main:app --reload --port 8000
健康检查：GET /health
接口文档：GET /docs (Swagger) / /openapi.json
"""
from contextlib import asynccontextmanager
"""
注释文档:
asynccontextmanager：把普通生成器函数改造成"异步上下文管理器"，用于实现 FastAPI 的 lifespan
（yield 之前=应用启动时执行，yield 之后=应用关闭时执行）。
"""
from pathlib import Path
"""
注释文档:
Path：路径操作。这里用来确保 uploads/ 目录存在（StaticFiles 挂载要求目录真实存在）。
"""

from fastapi import FastAPI
"""
注释文档:
FastAPI：应用类。实例化后即得到 ASGI 应用（uvicorn 托管的入口对象），
并自动附带 /docs(Swagger) 与 /openapi.json 两个内置接口。
"""
from fastapi.middleware.cors import CORSMiddleware
"""
注释文档:
CORSMiddleware：跨域中间件。浏览器前端(不同端口)请求本后端时，在响应头注入
Access-Control-Allow-*，否则浏览器会拦截跨域请求。
"""
from fastapi.staticfiles import StaticFiles
"""
注释文档:
StaticFiles：静态文件托管。把磁盘上的 uploads/ 目录直接映射为 /uploads/* 路径，
用于对外提供已上传图片的访问（H01/H05），不走路由与鉴权。
"""

from src.core.config import settings
"""
注释文档:
settings：全局配置单例（pydantic-settings 读 .env）。APP_NAME/APP_VERSION/UPLOAD_DIR 等
统一从这里取，避免配置硬编码散落。
"""
import logging as _logging
"""
注释文档:
v2.0 大点 01：应用级日志（uvicorn 只配置自己的 logger，业务模块 INFO 默认被 root=WARNING 吞掉）。
提速链路的关键耗时（预处理/判定/检索/生成、BM25 语料加载、预热结果）都打 INFO，
不配这一行就看不到 —— 排查慢在哪全靠这些日志。
"""
_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
from src.routers import (
    auth,
    chat,
    exam,
    hazards,
    kb,
    kb_agent_docs,
    knowledge_contrib,
    notifications,
    ppt,
    stats,
    uploads,
    user_profile,
    users,
    vision,
)
"""
注释文档:
各子路由模块（按功能拆分，用 APIRouter 定义，最后在下方 include_router 合并进主应用）：
- auth:         /api/auth      认证（注册/登录/当前用户）
- uploads:      /api/uploads   文件上传（H01 隐患图 / H05 整改照）
- notifications:/api/notifications 消息通知（H08，Redis 队列）
- stats:        /api/stats     统计分析（H07）
- vision:       /api/vision    图片隐患识别（视觉子模型 qwen3.8max）
- ppt:          /api/ppt       PPT 工坊（P01-P07，D4）
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期钩子：启动时执行 yield 之前的代码，关闭时执行 yield 之后的代码。

    本应用只在启动时做一件轻量工作——确保上传目录存在：
    - 数据表由 P3 线 database/schema.sql 统一管理，本应用不自动建表；
    - 重 AI 依赖（chromadb/sentence-transformers）由 P2 线 lazy-import，保证核心后端轻量启动；
    - uploads/ 目录用于存放上传图片（StaticFiles 依赖真实目录，这里作兜底，真正的
      首次创建在模块级 mount 之前完成，见下文）。
    """
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    # v2.0 大点 05：先保证 Redis 可用（复用外部 → 拉起内置 → 降级），再做检索预热。
    # 顺序不能反：预热里的会话缓存会摸 Redis；且惰性客户端要在 URL 覆写之后才建池。
    try:
        from src.core import redis_boot

        _redis_state = redis_boot.ensure()
    except Exception as exc:  # noqa: BLE001 —— 启动器自身炸了也不能拖垮服务
        _redis_state = {"state": "degraded", "message": f"Redis 启动器异常：{type(exc).__name__}: {exc}"}
        _logging.getLogger(__name__).warning(_redis_state["message"])

    # 启动横幅：uvicorn 只报「服务在 8000」，但用户实际打开的是 /safety 这个 SPA。
    base = (getattr(settings, "FRONT_BASE_URL", "") or "http://127.0.0.1:8000").rstrip("/")
    _logging.getLogger(__name__).info(
        "用户前端入口：%s/safety   后端 API 根：%s/api   接口文档：%s/docs",
        base, base, base,
    )

    # v2.0 大点 01：后台预热检索链路（BM25 语料/索引 + embedding + 重排模型）。
    # 冷启动实测 ~175s（Chroma 全量读取 159.8s + BM25 构建 14.9s），必须挪到启动阶段，
    # 否则用户第一问要等近 3 分钟（首问预算 60s）。失败只记日志，不影响服务可用。
    if getattr(settings, "RAG_WARMUP_ON_START", True):
        import threading

        def _warm() -> None:
            try:
                from src.ai.rag import warmup

                warmup()
            except Exception as exc:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning("检索链路预热失败: %s", exc)

        threading.Thread(target=_warm, name="rag-warmup", daemon=True).start()

    try:
        yield  # 此处挂起：应用运行期间一直停留在这里，退出时才继续向下执行
    finally:
        # 只关闭自己拉起的内置实例（外部实例不受影响，裁定 6）
        try:
            from src.core import redis_boot

            redis_boot.stop()
        except Exception:  # noqa: BLE001
            _logging.getLogger(__name__).debug("关闭内置 Redis 失败", exc_info=True)


# 创建 FastAPI 应用实例（uvicorn 要取的那个 app）
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",        # Swagger 交互式接口文档地址
    openapi_url="/openapi.json",  # 机器可读的 OpenAPI 契约（前端/测试可解析）
)

# ---- CORS 跨域配置 ----
# 开发期全放开（CORS_ORIGINS="*"）；上线由 P4 收紧：改为具体域名列表，
# 且此时 allow_credentials 才可安全置 True（"*" 与携带凭证互斥，浏览器规范）。
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,              # 允许的来源（* 或逗号分隔的域名列表）
    allow_credentials=settings.CORS_ORIGINS != "*",  # 是否允许携带 Cookie/凭证
    allow_methods=["*"],                      # 允许的 HTTP 方法
    allow_headers=["*"],                      # 允许的请求头
)

# ---- 历史静态通道封禁（v2.0 大点 00）----
# 试卷导出文件历史上落在 uploads/exam/ 并被 /uploads 静态暴露（等于两条无鉴权通道）。
# 新链路已改为带鉴权的下载端点；此处把老通道封死——既不迁移历史文件，也不影响老记录下载
# （下载端点会按数据库 file_path 兼容解析到 uploads/exam/）。
from fastapi.responses import JSONResponse  # noqa: E402


@app.middleware("http")
async def _block_legacy_exam_static(request, call_next):
    if request.url.path.startswith("/uploads/exam/"):
        return JSONResponse(
            {"detail": "该路径已停用，请通过试卷下载接口获取"}, status_code=403
        )
    return await call_next(request)


# ---- 挂载子路由 ----
# include_router 把各模块 APIRouter 定义的路由表合并进主应用（共享中间件与依赖），
# 各模块内部已通过 prefix 定义好自己的路径前缀。
app.include_router(auth.router)
app.include_router(hazards.router)
app.include_router(chat.router)
app.include_router(uploads.router)
app.include_router(notifications.router)
app.include_router(stats.router)
app.include_router(vision.router)
app.include_router(kb.router)
app.include_router(ppt.router)
app.include_router(users.router)
app.include_router(exam.router)
app.include_router(knowledge_contrib.router)
app.include_router(kb_agent_docs.router)
app.include_router(user_profile.router)

# ---- 静态文件托管 ----
# 把磁盘 uploads/ 目录直接映射为 /uploads/*，前端 <img src="/uploads/xxx.jpg"> 即可访问。
# 注意：StaticFiles 在导入时就要求目录存在，因此这里在模块级先确保创建；
# lifespan 里的 mkdir 仅作兜底（防止仅通过 lifespan 运行时目录缺失）。
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# 显式注册 markdown 的 MIME（Windows 上 mimetypes 默认把 .md 映射成 text/plain，
# 导致浏览器把试卷 md 文件当纯文本下载成 .txt；统一置为 text/markdown）
import mimetypes  # noqa: E402

mimetypes.add_type("text/markdown", ".md")

# PPT 产物目录（D4：生成的 .pptx 由 PptService 写入）
# v2.0 大点 00：**取消 /ppt-files 静态挂载**——产物属私有数据，唯一下载出口是
# GET /api/ppt/{task_id}/download（JWT + 归属校验）。目录仍要建，生成时要写盘。
from src.services.ppt_service import PPT_FILES_DIR  # noqa: E402

Path(PPT_FILES_DIR).mkdir(parents=True, exist_ok=True)

# 考试工坊导出目录（v2.0 起为 exam_files/，不再位于 uploads/ 之下）
# 同样取消静态挂载，唯一下载出口是 GET /api/exam/papers/{paper_id}/download。
from src.services.exam_export import EXAM_FILES_DIR  # noqa: E402

Path(EXAM_FILES_DIR).mkdir(parents=True, exist_ok=True)

# demo 前端（D3 vibe 对接：AI 问答流式聊天），静态托管 /demo
# 前端统一放项目根 front/ 目录（与 backend/ 平级），后端只做静态托管
FRONT_DIR = Path(__file__).resolve().parents[2] / "front"
Path(FRONT_DIR / "demo").mkdir(parents=True, exist_ok=True)
app.mount("/demo", StaticFiles(directory=FRONT_DIR / "demo", html=True), name="demo")

# 完整前端（川蜀风红色系 SPA：登录/工作台/隐患闭环/AI 问答/通知），静态托管 /safety
Path(FRONT_DIR / "safety").mkdir(parents=True, exist_ok=True)
app.mount("/safety", StaticFiles(directory=FRONT_DIR / "safety", html=True), name="safety")


# ---- 健康检查 ----
# @app.get 把函数注册进路由表：路径 /health + GET 方法；返回的 dict 会被
# FastAPI 自动序列化为 JSON 响应（200）。
@app.get("/health", tags=["系统"], summary="健康检查")
def health():
    """服务存活探针：供部署监控/前端判断后端是否可用。"""
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}
