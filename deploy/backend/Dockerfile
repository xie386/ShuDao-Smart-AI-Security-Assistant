# 蜀道智能安全助手 — 后端镜像（CPU 推理版）
#
# 关键决策：
#   1. requirements.txt 锁定 torch==2.6.0+cu124（本机 GPU 版），Docker 镜像为 CPU 推理
#      单独先装 CPU 版 torch（--index-url cpu 源），再装其余依赖（torch 已满足跳过）
#      → 镜像体积约 2.5GB 减至 ~1.2GB，且不依赖宿主 GPU 驱动
#   2. 目录布局保持 backend/src/main.py（与宿主机一致）：
#      main.py 用 parents[2]/front 推导前端目录，容器内 /app/backend/src/main.py
#      → parents[2]=/app → /app/front（mkdir 兜底；实际前端由 nginx 托管）
#   3. 镜像内不 COPY 大体积数据（chroma/uploads/ppt_files 均 bind mount 复用本机）
#   4. 启动入口 entrypoint.sh：等 MySQL 就绪 → 初始化 admin 账号 → uvicorn
FROM python:3.11-slim

# 系统依赖：sentence-transformers/torch 需要 libgomp1（OpenMP），Pillow 需要 zlib
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        zlib1g \
        libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# ---- 先装 CPU 版 torch（跳过 requirements 里的 +cu124） ----
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        torch==2.6.0 \
    && pip install --no-cache-dir "numpy<3"

# ---- 其余依赖 ----
COPY backend/requirements.txt /app/backend/requirements.txt
# 移除 torch 行（已装 CPU 版），其余原样安装
RUN sed '/^torch==/d' /app/backend/requirements.txt > /app/backend/requirements-docker.txt \
    && pip install --no-cache-dir -r /app/backend/requirements-docker.txt

# ---- 应用代码（保持 backend/src 布局） ----
COPY backend/src /app/backend/src
COPY backend/scripts /app/backend/scripts
COPY backend/prompt /app/backend/prompt
COPY backend/knowledge_src /app/backend/knowledge_src
COPY deploy/backend/entrypoint.sh /app/backend/entrypoint.sh
RUN chmod +x /app/backend/entrypoint.sh

# 运行时目录兜底：chroma/uploads/ppt_files 由 volume 挂载（见 compose），
# front 由 main.py parents[2]/front 推导（nginx 实际托管，此处仅防挂载失败）
RUN mkdir -p /app/backend/chroma /app/backend/uploads /app/backend/ppt_files /app/front/demo /app/front/safety

EXPOSE 8000
CMD ["/app/backend/entrypoint.sh"]
