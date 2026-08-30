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
from src.routers import auth, chat, exam, hazards, kb, knowledge_contrib, notifications, ppt, stats, uploads, user_profile, users, vision
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
    yield  # 此处挂起：应用运行期间一直停留在这里，退出时才继续向下执行（可写清理逻辑）


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

# PPT 产物目录（D4：生成的 .pptx 由 PptService 写入，/ppt-files 静态托管供导出下载 P07）
from src.services.ppt_service import PPT_FILES_DIR  # noqa: E402

Path(PPT_FILES_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/ppt-files", StaticFiles(directory=PPT_FILES_DIR), name="ppt-files")

# 考试工坊导出目录（优化丰富期：试卷 md/docx/pdf 由 exam_export 写入，/exam-files 静态托管供下载）
from src.services.exam_export import EXAM_FILES_DIR  # noqa: E402

Path(EXAM_FILES_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/exam-files", StaticFiles(directory=EXAM_FILES_DIR), name="exam-files")

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
