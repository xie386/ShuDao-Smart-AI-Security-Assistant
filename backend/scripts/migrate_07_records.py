"""迁移：新增 `message_records`（v2.0 大点 07 · 裁定 5）。

只加表，不动 `schema.sql` 既有表。幂等，默认 dry-run。

用法：
    python scripts/migrate_07_records.py           # 打印 DDL
    python scripts/migrate_07_records.py --apply   # 建表
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

B = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B))

DDL = """
CREATE TABLE IF NOT EXISTS `message_records` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `kind` varchar(24) NOT NULL COMMENT 'hazard_closed / kb_contrib',
  `ref_id` int NOT NULL COMMENT 'hazard_id / contribution_id',
  `user_id` int DEFAULT NULL COMMENT '可见性归属：上报人 / 贡献者',
  `title` varchar(200) NOT NULL,
  `summary` varchar(500) DEFAULT NULL,
  `status` varchar(24) DEFAULT NULL COMMENT '已闭环 / 已入库 / 已驳回',
  `payload_json` mediumtext NOT NULL COMMENT '全量快照',
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_kind_ref` (`kind`,`ref_id`),
  KEY `ix_records_kind_time` (`kind`,`created_at`),
  KEY `ix_records_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
"""

ROLLBACK = "DROP TABLE IF EXISTS `message_records`;"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新增 message_records（幂等）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认 dry-run）")
    args = ap.parse_args(argv)

    print("=== DDL ===")
    print(DDL.strip())
    if not args.apply:
        print("\n（dry-run，未执行；加 --apply 落库）")
        return 0

    from sqlalchemy import text

    from src.core.db import engine

    with engine.begin() as conn:
        conn.execute(text(DDL))
        exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'message_records'"
        )).scalar()
    print(f"\n完成：表存在 = {bool(exists)}")
    print(f"回滚：{ROLLBACK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
