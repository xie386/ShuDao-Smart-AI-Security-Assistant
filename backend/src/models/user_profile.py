"""个人主页扩展资料模型：对齐 shudao_safety.user_profiles 表。

设计：users 表零改动（守 P3「勿改表」铁律），email/avatar 等扩展信息落到本一对一表。
user_id 唯一约束，与 User 一对一。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    email: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # 头像 URL（/uploads/<uuid>.<ext>）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
