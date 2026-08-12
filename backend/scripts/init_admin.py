#!/usr/bin/env python
"""初始化管理员账号（开发便利脚本）。

用法（在 backend/ 目录下，使用全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/init_admin.py [username] [password]

默认：admin / admin123（首次登录后请立即修改）。
已存在同名用户时跳过，不覆盖密码。
"""
import sys
from pathlib import Path

# 允许从 backend/ 下直接运行脚本
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.core.db import SessionLocal
from src.core.security import hash_password
from src.models.user import User


def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "admin"
    password = sys.argv[2] if len(sys.argv) > 2 else "admin123"

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == username))
        if existing:
            print(f"[跳过] 用户 {username!r} 已存在（id={existing.id}），未修改密码")
            return
        admin = User(
            username=username,
            hashed_password=hash_password(password),
            role="admin",
            real_name="系统管理员",
        )
        db.add(admin)
        db.commit()
        print(f"[OK] 管理员账号已创建: {username} / {password}（请尽快修改密码）")


if __name__ == "__main__":
    main()
