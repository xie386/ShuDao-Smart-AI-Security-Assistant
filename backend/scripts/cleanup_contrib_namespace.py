#!/usr/bin/env python
"""清理旧命名空间的贡献块（v2.0 大点 04 · 裁定 11）。

背景：04 之前贡献入库用 `doc=contrib_{id}`、`source=user_contrib`；04 起改为
`doc={md 文件名}`、`source=knowledge_src/...`。旧块不删就会与新文件块重复。

用法（backend/ 目录）：
    C:/python/python.exe scripts/cleanup_contrib_namespace.py --dry-run   # 默认，只报告
    C:/python/python.exe scripts/cleanup_contrib_namespace.py --apply     # 真正删除
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def scan() -> list[dict]:
    """找出 source=user_contrib 或 doc 以 contrib_ 开头的旧块（按 id 去重，避免重复计数）。"""
    from src.ai.knowledge import get_collection

    coll = get_collection()
    by_doc: dict[str, set[str]] = {}

    def _collect(hit: dict) -> None:
        for cid, md in zip(hit.get("ids") or [], hit.get("metadatas") or []):
            doc = (md or {}).get("doc", "")
            if doc and (doc.startswith("contrib_") or (md or {}).get("source") in ("user_contrib", "user_contrib")):
                by_doc.setdefault(doc, set()).add(cid)

    try:
        _collect(coll.get(where={"source": "user_contrib"}, limit=50000))
    except Exception:  # noqa: BLE001 —— 元数据键不存在时 Chroma 会报错
        pass
    try:
        _collect(coll.get(include=["metadatas"], limit=200000))
    except Exception:  # noqa: BLE001
        pass
    return [{"doc": d, "chunks": len(ids)} for d, ids in sorted(by_doc.items())]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只报告（默认行为，显式写出来便于文档引用）")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    from src.ai.knowledge import get_collection

    items = scan()
    total = sum(i["chunks"] for i in items)
    print(f"发现旧命名空间块：{len(items)} 个 doc / 共 {total} 块")
    for i in items:
        print(f"  - {i['doc']}: {i['chunks']} 块")
    if not items:
        return
    if not apply:
        print("\n（dry-run，未删除；加 --apply 真正删除）")
        return
    coll = get_collection()
    for i in items:
        coll.delete(where={"doc": i["doc"]})
        print(f"  已删除 {i['doc']}")
    print("\n完成。建议随后跑一次检索抽查，确认没有命中缺失文档。")


if __name__ == "__main__":
    main()
