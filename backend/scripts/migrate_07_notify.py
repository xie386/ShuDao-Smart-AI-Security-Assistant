"""迁移：把 v2.0 大点 07 之前的消息 list 搬进 HASH + ZSET（裁定 2 的兼容项）。

旧结构：`shudao:notify:<uid>` 是 LIST，元素为 JSON 字符串（或更老的纯文本）。
新结构：同前缀是 HASH（msg_id → JSON），`shudao:notify:z:<uid>` 是 ZSET（msg_id → created_at）。

搬完把旧 list 改名成 `shudao:notify:<uid>:legacy` 并设 1 天 TTL（可回滚、不立刻丢数据）。

用法：
    python scripts/migrate_07_notify.py            # 只统计，不写
    python scripts/migrate_07_notify.py --apply    # 执行搬迁
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

B = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B))

PREFIX = "shudao:notify:"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="旧消息 list → hash/zset")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ttl", type=int, default=86400, help="legacy 键保留秒数（默认 1 天）")
    args = ap.parse_args(argv)

    from src.core.redis_client import get_redis

    r = get_redis()
    keys = [k for k in r.scan_iter(f"{PREFIX}*") if not k.startswith(f"{PREFIX}z:")]
    print(f"候选键：{len(keys)} 个")
    moved = total = 0
    for key in keys:
        if r.type(key) != "list":
            print(f"  跳过 {key}（类型 {r.type(key)}）")
            continue
        raw_items = r.lrange(key, 0, -1)
        if not raw_items:
            continue
        uid = key[len(PREFIX):]
        now = time.time()
        pipe = r.pipeline()
        for i, raw in enumerate(raw_items):
            try:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError
            except (ValueError, TypeError):
                obj = {"type": "general", "message": str(raw), "hazard_id": None}
            msg = {
                "id": uuid.uuid4().hex[:12],
                "type": obj.get("type") or "general",
                "message": obj.get("message") or "",
                "hazard_id": obj.get("hazard_id"),
                "contrib_id": obj.get("contrib_id"),
                "created_at": int(now) - (len(raw_items) - i),   # 保持原顺序
                "expire_at": None,
                "read": False,
                "status": None,
            }
            mid = msg["id"]
            pipe.hset(key, mid, json.dumps(msg, ensure_ascii=False))
            pipe.zadd(f"{PREFIX}z:{uid}", {mid: msg["created_at"]})
        if args.apply:
            pipe.execute()
            r.rename(key, f"{key}:legacy")
            r.expire(f"{key}:legacy", args.ttl)
            moved += 1
        total += len(raw_items)
        print(f"  {key}: {len(raw_items)} 条 → {uid}{'（已写）' if args.apply else '（dry-run）'}")

    print(f"\n合计 {total} 条 / {moved if args.apply else 0} 个键已迁移")
    if not args.apply:
        print("（dry-run，未写入；加 --apply 执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
