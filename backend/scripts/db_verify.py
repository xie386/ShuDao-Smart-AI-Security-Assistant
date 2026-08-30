#!/usr/bin/env python
"""数据库模型对齐验证：ORM 模型 vs MySQL 真实表结构（列名级）。 

用法（在 backend/ 目录下）：
    PYTHONPATH= C:\\python\\python.exe scripts/db_verify.py

输出 [OK] 表示模型与该表完全对齐；[DIFF] 会列出差异列。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect  # noqa: E402

from src import models  # noqa: E402
from src.core.db import engine  # noqa: E402

# (表名, ORM 模型)
MODEL_MAP = [
    ("users", models.User),
    ("hazards", models.Hazard),
    ("hazard_timeline", models.HazardTimeline),
    ("conversations", models.Conversation),
    ("chat_messages", models.ChatMessage),
    ("ppt_tasks", models.PptTask),
    ("exam_papers", models.ExamPaper),
    ("knowledge_contributions", models.KnowledgeContribution),
    ("knowledge_contrib_timeline", models.KnowledgeContribTimeline),
    ("user_profiles", models.UserProfile),
]


def main() -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    print(f"库内现有表: {sorted(tables)}")

    all_ok = True
    for name, model in MODEL_MAP:
        if name not in tables:
            print(f"[缺表] {name}: 库中不存在（先执行 database/schema.sql）")
            all_ok = False
            continue
        db_cols = {c["name"] for c in insp.get_columns(name)}
        model_cols = {c.name for c in model.__table__.columns}
        missing = model_cols - db_cols   # 模型有、库没有
        extra = db_cols - model_cols     # 库有、模型没有
        if not missing and not extra:
            print(f"[OK] {name}: 模型/库列完全对齐（{len(model_cols)} 列）")
        else:
            all_ok = False
            print(
                f"[DIFF] {name}: 模型 {len(model_cols)} 列 vs 库 {len(db_cols)} 列"
                + (f"；模型缺列 {missing}" if missing else "")
                + (f"；库多列 {extra}" if extra else "")
            )
    print("\n结论:", "全部对齐 ✅" if all_ok else "存在差异，需同步 schema.sql / 模型 ⚠️")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
