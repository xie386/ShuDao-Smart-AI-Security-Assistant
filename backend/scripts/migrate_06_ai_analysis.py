"""迁移：新增 `hazard_ai_analysis`（v2.0 大点 06 · 裁定 5）。

**不动 `database/schema.sql` 既有表**，只加一张新表。幂等：重复执行安全。

用法：
    python scripts/migrate_06_ai_analysis.py            # 只打印将执行的 SQL（dry-run）
    python scripts/migrate_06_ai_analysis.py --apply    # 真正建表
    python scripts/migrate_06_ai_analysis.py --apply --drop   # 危险：先删后建（仅本地）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

B = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(B))

DDL = """
CREATE TABLE IF NOT EXISTS `hazard_ai_analysis` (
  `id` int NOT NULL AUTO_INCREMENT,
  `hazard_id` int NOT NULL,
  `model` varchar(64) DEFAULT NULL,
  `boxes_json` mediumtext,
  `raw_json` mediumtext,
  `annotated_url` varchar(255) DEFAULT NULL,
  `generated_by` varchar(16) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT (now()),
  PRIMARY KEY (`id`),
  KEY `ix_hazard_ai_analysis_hazard_id` (`hazard_id`),
  CONSTRAINT `hazard_ai_analysis_ibfk_1` FOREIGN KEY (`hazard_id`) REFERENCES `hazards` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
"""

DROP = "DROP TABLE IF EXISTS `hazard_ai_analysis`;"

ROLLBACK = """
-- 回滚（如需撤销）：
DROP TABLE IF EXISTS `hazard_ai_analysis`;
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新增 hazard_ai_analysis 表（幂等）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认 dry-run）")
    ap.add_argument("--drop", action="store_true", help="先 DROP 再建（仅本地调试）")
    args = ap.parse_args(argv)

    print("=== DDL ===")
    if args.drop:
        print(DROP)
    print(DDL.strip())
    if not args.apply:
        print("\n（dry-run，未执行；加 --apply 落库）")
        return 0

    from sqlalchemy import text

    from src.core.db import engine

    with engine.begin() as conn:
        if args.drop:
            conn.execute(text(DROP))
            print("已 DROP")
        conn.execute(text(DDL))
        exist = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'hazard_ai_analysis'"
        )).scalar()
    print(f"\n完成：表存在 = {bool(exist)}")
    print(ROLLBACK.strip())
    return 0


if __name__ == "__main__":
    os.chdir(B)
    sys.exit(main())
