# 蜀道智能安全助手 — 后端

FastAPI 分层后端（P1 线脚手架 + 认证模块）。

## 环境说明（重要）

- **不创建虚拟环境**，直接使用全局 Python：`C:\python`（Python 3.12.4）
- 上交环境需求文件：见 `requirements.txt`（从全局环境提取的项目依赖）
- 若在工具 shell（Hermes 等）中运行，先清空 PYTHONPATH 再执行：
  `PYTHONPATH= C:\python\python.exe -m uvicorn src.main:app --reload --port 8000`
- 本机代理会劫持 localhost 请求，curl 需加 `--noproxy '*'`

## 目录结构

```
backend/
├── src/
│   ├── main.py            # 应用入口（CORS / 路由挂载 / /health）
│   ├── core/              # config(pydantic-settings) / security(JWT+bcrypt) / db(SQLAlchemy)
│   ├── models/            # ORM 模型（对齐 P3 的 database/schema.sql，勿改表结构）
│   ├── schemas/           # Pydantic 请求/响应契约
│   ├── routers/           # API 路由层（只做校验与路由）
│   ├── services/          # 业务逻辑层（事务/状态机）
│   └── scripts/           # 工具脚本（init_admin.py）
├── tests/                 # pytest（冒烟 + 认证闭环）
├── .env / .env.example    # 环境变量（.env 不提交）
└── requirements.txt       # 环境需求文件（全局环境依赖清单）
```

## 快速启动

```bash
cd backend
PYTHONPATH= C:\python\python.exe -m uvicorn src.main:app --reload --port 8000
```

- 健康检查: http://127.0.0.1:8000/health
- Swagger 文档: http://127.0.0.1:8000/docs

## 认证接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/auth/register | 注册（角色固定 employee，安全约定不接受 role） |
| POST | /api/auth/login | 登录，返回 JWT |
| GET  | /api/auth/me | 当前用户（Bearer token） |

初始化管理员（开发便利）：
```bash
PYTHONPATH= C:\python\python.exe scripts/init_admin.py admin admin123
```

## 测试

```bash
PYTHONPATH= C:\python\python.exe -m pytest tests/ -v
```

依赖本机 MySQL（shudao_safety 库，root/123456）。表结构由 P3 线 schema 管理，应用不自动建表。
