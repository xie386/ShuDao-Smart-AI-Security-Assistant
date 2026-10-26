#!/usr/bin/env python
"""v2.0 大点 04 数据库迁移（幂等、只做加法）。

新增：
- knowledge_contributions：source_kind / source_agent_id / origin_urls / origin_filename / md_path / convert_by
- knowledge_contrib_timeline：actor_kind
- 新表：agent_clients / agent_runs（内置一条 knowledge_scout）

用法（backend/ 目录）：
    C:/python/python.exe scripts/migrate_04_kb_contrib.py --dry-run
    C:/python/python.exe scripts/migrate_04_kb_contrib.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.core.db import engine  # noqa: E402

CONTRIB_COLUMNS = {
    "source_kind": "ENUM('user','agent') NOT NULL DEFAULT 'user' COMMENT 'v2.0 大点 04：贡献来源'",
    "source_agent_id": "INT NULL COMMENT 'v2.0 大点 04：agent_clients.id'",
    "origin_urls": "TEXT NULL COMMENT 'v2.0 大点 04：JSON 数组，Agent 采集来源 URL'",
    "origin_filename": "VARCHAR(255) NULL COMMENT 'v2.0 大点 04：上传时的原始文件名'",
    "md_path": "VARCHAR(255) NULL COMMENT 'v2.0 大点 04：落盘相对路径'",
    "convert_by": "VARCHAR(32) NULL COMMENT 'v2.0 大点 04：mineru/builtin/fallback/direct'",
}

TABLES = {
    "agent_clients": """
        CREATE TABLE IF NOT EXISTS `agent_clients` (
          `id` int NOT NULL AUTO_INCREMENT,
          `name` varchar(64) NOT NULL,
          `api_key_hash` varchar(128) DEFAULT NULL,
          `model` varchar(64) DEFAULT NULL,
          `owner_user_id` int DEFAULT NULL,
          `scopes` varchar(255) NOT NULL DEFAULT 'contrib:submit',
          `enabled` tinyint(1) NOT NULL DEFAULT '1',
          `created_at` datetime NOT NULL DEFAULT (now()),
          PRIMARY KEY (`id`),
          UNIQUE KEY `uq_agent_clients_name` (`name`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """,
    "agent_runs": """
        CREATE TABLE IF NOT EXISTS `agent_runs` (
          `id` int NOT NULL AUTO_INCREMENT,
          `conversation_id` int DEFAULT NULL,
          `gap_question` text,
          `urls` text,
          `model` varchar(64) DEFAULT NULL,
          `methodology_version` varchar(32) DEFAULT NULL,
          `result_md_path` varchar(255) DEFAULT NULL,
          `contrib_id` int DEFAULT NULL,
          `status` varchar(24) NOT NULL DEFAULT 'running',
          `note` text,
          `created_at` datetime NOT NULL DEFAULT (now()),
          PRIMARY KEY (`id`),
          KEY `ix_agent_runs_conv` (`conversation_id`),
          KEY `ix_agent_runs_status` (`status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
    """,
}


def columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}


def table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": table},
    ).scalar()
    return bool(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只打印计划）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划（默认行为）")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    plan: list[str] = []
    with engine.begin() as conn:
        cols = columns(conn, "knowledge_contributions")
        for name, ddl in CONTRIB_COLUMNS.items():
            if name not in cols:
                plan.append(f"ALTER TABLE knowledge_contributions ADD COLUMN `{name}` {ddl}")
        tl_cols = columns(conn, "knowledge_contrib_timeline")
        if "actor_kind" not in tl_cols:
            plan.append(
                "ALTER TABLE knowledge_contrib_timeline ADD COLUMN `actor_kind` "
                "VARCHAR(16) NOT NULL DEFAULT 'human' COMMENT 'v2.0 大点 04：human/agent'"
            )
        for t, ddl in TABLES.items():
            if not table_exists(conn, t):
                plan.append(ddl.strip())

        print(f"待执行变更：{len(plan)} 条")
        for item in plan:
            print("  -", " ".join(item.split())[:110])
        if not apply:
            print("\n（dry-run，未执行；加 --apply 真正落库）")
            return

        for item in plan:
            conn.execute(text(item))
            print("  已执行:", " ".join(item.split())[:80])
        # 内置 Agent 身份（幂等）
        conn.execute(
            text(
                "INSERT INTO agent_clients (name, model, scopes, enabled) "
                "VALUES ('knowledge_scout', :m, 'contrib:submit,fetch', 1) "
                "ON DUPLICATE KEY UPDATE model = VALUES(model), scopes = VALUES(scopes)"
            ),
            {"m": "deepseek-v4-flash"},
        )
        print("  已确保内置 agent_clients: knowledge_scout")
    print("\n迁移完成。回滚（如需）：")
    print("  ALTER TABLE knowledge_contributions DROP COLUMN source_kind, DROP COLUMN source_agent_id,")
    print("    DROP COLUMN origin_urls, DROP COLUMN origin_filename, DROP COLUMN md_path, DROP COLUMN convert_by;")
    print("  ALTER TABLE knowledge_contrib_timeline DROP COLUMN actor_kind;")
    print("  DROP TABLE agent_runs; DROP TABLE agent_clients;")


if __name__ == "__main__":
    main()
