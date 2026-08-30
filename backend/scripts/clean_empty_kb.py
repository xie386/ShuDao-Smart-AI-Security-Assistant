#!/usr/bin/env python
"""知识库空壳文档清理：删除治理后正文过短（<阈值）的 md 及其源 htm。

为什么需要：批量采集会命中「附件下载页」——网页本身只有标题+下载链接，
正文在 .doc/.pdf 附件里（安全管理网 Standard 频道大量如此）。
这类文档治理后 md 只有标题（<300 字），入库只会污染检索（噪声块），
必须从知识库剔除，并同步清理 kb_manifest.jsonl 记录。

用法（backend/ 目录）：
    PYTHONPATH= C:\\python\\python.exe scripts/clean_empty_kb.py --dry-run
    PYTHONPATH= C:\\python\\python.exe scripts/clean_empty_kb.py --min-chars 300
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = Path(__file__).resolve().parent.parent / "knowledge_src"
MANIFEST = BASE / "kb_manifest.jsonl"
DOC_TYPES = ["laws", "procedures", "plans", "cases"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-chars", type=int, default=300, help="md 正文字数下限（含标题）")
    ap.add_argument("--dry-run", action="store_true", help="只列出不删除")
    ap.add_argument(
        "--keep-htm", action="store_true", help="只删 md 保留 htm（便于日后改治理规则重跑）"
    )
    args = ap.parse_args()

    victims: list[tuple[str, Path]] = []
    for dt in DOC_TYPES:
        d = BASE / dt
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            if len(text) < args.min_chars:
                victims.append((dt, f))

    print(f"发现空壳文档 {len(victims)} 份（阈值 {args.min_chars} 字）")
    by_type: dict[str, int] = {}
    for dt, _ in victims:
        by_type[dt] = by_type.get(dt, 0) + 1
    for dt, n in sorted(by_type.items()):
        print(f"  {dt:11} {n}")

    if args.dry_run:
        print("\n[DRY-RUN] 未删除。样例：")
        for dt, f in victims[:8]:
            print(f"  {dt}/{f.name}")
        return

    removed_ids: set[str] = set()
    for _dt, f in victims:
        removed_ids.add(f.stem)
        f.unlink(missing_ok=True)
        if not args.keep_htm:
            f.with_suffix(".htm").unlink(missing_ok=True)

    # 同步清理 manifest
    if MANIFEST.exists() and removed_ids:
        kept = []
        with MANIFEST.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if r.get("source_id") not in removed_ids:
                    kept.append(r)
        with MANIFEST.open("w", encoding="utf-8") as fh:
            for r in kept:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nmanifest 已清理：剩余 {len(kept)} 条")

    print(f"已删除 {len(victims)} 份空壳文档" + ("（htm 保留）" if args.keep_htm else "（含 htm）"))

    # 汇总现状
    print("\n=== 清理后统计 ===")
    total = 0
    for dt in DOC_TYPES:
        n = len(list((BASE / dt).glob("*.md"))) if (BASE / dt).is_dir() else 0
        total += n
        print(f"  {dt:11} {n}")
    print(f"  {'总计':11} {total}")


if __name__ == "__main__":
    main()
