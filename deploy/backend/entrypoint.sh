#!/bin/bash
# 蜀道后端容器启动入口：
#   1. 等待 MySQL 就绪（重试 60 次 × 2s，容器内无 mysqladmin，用 python 探测 TCP 端口）
#   2. 等待 Redis 就绪
#   3. 初始化 admin 账号（scripts/init_admin.py，幂等：已存在则跳过）
#   4. 启动 uvicorn（--workers 1：SSE 流式 + 本地 Chroma 单写，多 worker 会重复加载模型）
set -e

echo "[entrypoint] 等待 MySQL (mysql:3306) ..."
python - <<'PY'
import socket, time
for i in range(60):
    try:
        s = socket.create_connection(("mysql", 3306), timeout=2)
        s.close()
        print("[entrypoint] MySQL 就绪")
        break
    except OSError:
        time.sleep(2)
else:
    raise SystemExit("[entrypoint] MySQL 60s 内未就绪，退出")
PY

echo "[entrypoint] 等待 Redis (redis:6379) ..."
python - <<'PY'
import socket, time
for i in range(30):
    try:
        s = socket.create_connection(("redis", 6379), timeout=2)
        s.close()
        print("[entrypoint] Redis 就绪")
        break
    except OSError:
        time.sleep(2)
else:
    print("[entrypoint] 警告：Redis 未就绪，继续启动（通知功能将降级）")
PY

echo "[entrypoint] 初始化 admin 账号 ..."
python scripts/init_admin.py || echo "[entrypoint] init_admin 失败（非致命，继续启动）"

echo "[entrypoint] 启动 uvicorn ..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 1
