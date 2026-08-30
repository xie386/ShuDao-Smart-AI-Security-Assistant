# Docker 一键部署（D5 P4：D1 Docker + D2 Nginx 静态托管与反向代理）

> 在项目根 `team/` 下新建 `deploy/`（与 backend/front 平级），全部部署代码在此。
> **你只需执行下方命令，构建/启动由 Docker 完成。**

## 架构

```
浏览器 → :8080 Nginx（deploy/nginx/nginx.conf）
           ├── /safety /demo → 静态托管 front/（无需改前端代码，前端全用相对路径 /api）
           ├── /api/*        → 反代 backend:8000（REST + SSE 流式，proxy_buffering off）
           ├── /uploads /ppt-files → 反代后端静态资源
           └── /health /docs → 反代后端
backend  (FastAPI，容器内 CPU 推理)
  ├── MySQL 8.0  : 业务库，首启自动执行 database/schema.sql
  ├── Redis 7    : 会话/限流/消息队列（H08）
  └── Chroma     : 本地目录 /app/backend/chroma（bind mount 复用本机已入库 2256 块）
```

## 快速开始

```bash
cd deploy
docker compose up -d --build     # 首次构建约 10-20 分钟（装 torch CPU 版 ~1.2GB）
```

验证：

| 地址 | 说明 |
|---|---|
| http://localhost:8080/safety | 完整前端（川蜀风 SPA） |
| http://localhost:8080/demo | 简版前端 |
| http://localhost:8080/health | 后端探针（`{"status":"ok"}`） |
| http://localhost:8080/docs | Swagger |

## 与「五服务」文档的差异（说明）

技术书写的是 mysql/redis/chroma/backend/nginx 五服务，但**代码事实**是：
- `src/ai/knowledge.py` 的 Chroma 用 `PersistentClient` **直连本地目录**（`CHROMA_DIR`），不是独立 Chroma server → compose 无需 chroma 容器，改用 bind mount 复用本机 `backend/chroma`（已入库 2256 块，开箱即用）；
- 若后续要迁移到独立 Chroma 服务，需改 `knowledge.py` 的连接方式，当前无此需求。

## 数据与模型挂载（bind mount 复用本机）

| 宿主路径 | 容器路径 | 用途 |
|---|---|---|
| `backend/chroma` | `/app/backend/chroma` | 向量库（2256 块，已入库） |
| `backend/uploads` | `/app/backend/uploads` | 上传的隐患图 |
| `backend/ppt_files` | `/app/backend/ppt_files` | 生成的 pptx |
| `D:/LLM/model` | `/models`（只读） | bge-large-zh / bge-reranker-base |

> 模型路径在 compose 的 `environment` 覆盖为 `/models/bge-*`（容器内 CPU 推理）。
> 如模型放别处，改 compose 里 `D:/LLM/model:/models:ro` 一行即可。

## 常用命令

```bash
docker compose ps                 # 查看状态（4 服务应全部 running/healthy）
docker compose logs -f backend    # 看后端日志
docker compose logs nginx         # 看 Nginx 日志
docker compose down               # 停止（保留数据卷）
docker compose down -v            # 停止并删除数据卷（⚠️ 会清空 mysql/redis 数据）
```

## 已知注意点

1. **首次构建慢**：torch CPU 版从 pytorch 官方源下载 ~200MB，整镜像 ~1.2GB，耐心等；
2. **端口**：前端映射宿主 `8080`（避开开发期 8000）。如需标准 80，改 compose 端口 `"80:80"`（Windows 需管理员权限）；
3. **GPU 推理**：容器默认 CPU（避免 GPU 驱动依赖）。如需 GPU 直通，给 backend 加 `deploy.resources.reservations.devices` 并改回 `EMBEDDING_DEVICE=cuda`（需 nvidia-container-toolkit）；
4. **.env**：API Key 复用 `backend/.env`（compose `env_file`），未配置的服务（如 PPT 百度云占位）会优雅降级；
5. **Redis 密码**：容器内 `--requirepass 123456`，与 `backend/.env` 的 `REDIS_URL` 一致；
6. **schema.sql**：mysql 容器**首次**启动自动执行（IF NOT EXISTS 幂等），admin 账号由 entrypoint 的 `init_admin.py` 创建（admin/admin123）。
