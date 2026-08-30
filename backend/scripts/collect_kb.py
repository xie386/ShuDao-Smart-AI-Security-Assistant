#!/usr/bin/env python
"""知识库入库脚本：扫描四类目录 → 结构感知分块 → 嵌入 → 写入 Chroma safety_kb。

用法（在 backend/ 目录下，使用全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/collect_kb.py             # 全量入库
    PYTHONPATH= C:\\python\\python.exe scripts/collect_kb.py --doc-type cases   # 只入库指定类型（增量）

说明：
- 稳定 ID（内容哈希）upsert：同一文本重跑覆盖，天然增量去重；
- 注意：数据治理（govern_html）后文本变化 → 稳定 ID 变化 → 需先清空集合再全量入库。
"""
import argparse
import logging
import sys
from pathlib import Path

# 允许从 backend/ 下直接运行脚本
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.knowledge import build_from_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-type", default="", help="只入库指定目录（laws/cases/plans/procedures）")
    args = ap.parse_args()

    stats = build_from_dir(doc_type=args.doc_type or None)
    print("\n===== 入库完成 =====")
    print(f"总入库块数: {stats['total_chunks']}")
    for doc_type, n in stats["by_type"].items():
        print(f"  {doc_type}: {n}")


if __name__ == "__main__":
    main()
