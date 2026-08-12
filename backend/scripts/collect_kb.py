#!/usr/bin/env python
"""知识库采集脚本框架（D1 脚手架，D3 完善 pipeline）。
 
用法（在 backend/ 目录下，使用全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/collect_kb.py

流程：扫描 knowledge_src/{laws,procedures,plans,cases}/
      → 结构感知分块 → 本地 bge-large-zh 嵌入 → 写入 Chroma safety_kb。

知识库源文档放置：
    backend/knowledge_src/
    ├── laws/        安全生产法、建设工程安全生产管理条例、企业安全管理制度等
    ├── procedures/  安全操作规程（按步骤编号）
    ├── plans/       应急预案（含预警/响应/处置/恢复阶段）
    └── cases/       典型事故案例（含经过/原因/教训字段）

说明：本脚本为 D1 框架版，D3 将补充：
- 增量 upsert 与去重（按 doc+章节 哈希）
- golden QA 集 Recall@K 校验
- 文档清洗（去除目录/页眉页脚）
"""
import logging
import sys
from pathlib import Path

# 允许从 backend/ 下直接运行脚本
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.knowledge import build_from_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    stats = build_from_dir()
    print("\n===== 采集完成 =====")
    print(f"总入库块数: {stats['total_chunks']}")
    for doc_type, n in stats["by_type"].items():
        print(f"  {doc_type}: {n}")


if __name__ == "__main__":
    main()
