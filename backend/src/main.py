"""蜀道智能安全助手 — 后端入口。

启动：
    cd backend && uvicorn src.main:app --reload --port 8000
健康检查：GET /health
接口文档：GET /docs (Swagger) / /openapi.json
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import settings
from src.routers import auth


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 数据表由 P3 线 database/schema.sql 统一管理，本应用不自动建表。
    # 重 AI 依赖（chromadb/sentence-transformers）由 P2 线 lazy-import，保证核心后端轻量启动。
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# CORS：开发期全放开，上线由 P4 收紧（allow_origins 具体化后 allow_credentials=True）
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=settings.CORS_ORIGINS != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)


@app.get("/health", tags=["系统"], summary="健康检查")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}
