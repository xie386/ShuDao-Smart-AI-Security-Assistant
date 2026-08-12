"""隐患模型：对齐 shudao_safety.hazards / hazard_timeline 表（P3 管理，勿改表结构）。

状态机（PRD）：待处理 → 处理中 → 待验收 → 已闭环；待验收 → 已驳回 → 处理中。
updated_at 表无 ON UPDATE 触发器，由 SQLAlchemy onupdate 应用层维护。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class Hazard(Base):
    __tablename__ = "hazards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Enum("重大", "较大", "一般", "轻微"))
    hazard_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("待处理", "处理中", "待验收", "已闭环", "已驳回")
    )
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    image_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 上报图片 JSON 数组字符串
    rectify_measures: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # H05 整改措施
    rectify_images: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # H05 整改后照片 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Hazard id={self.id} title={self.title!r} status={self.status!r}>"


class HazardTimeline(Base):
    """隐患处理进度时间线（H03 详情页展示）。"""

    __tablename__ = "hazard_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hazard_id: Mapped[int] = mapped_column(ForeignKey("hazards.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))  # 上报/派单/整改/验收/驳回...
    operator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HazardTimeline id={self.id} hazard={self.hazard_id} action={self.action!r}>"
