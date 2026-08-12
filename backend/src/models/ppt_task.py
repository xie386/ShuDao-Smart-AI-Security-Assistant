"""PPT 制作任务模型：对齐 shudao_safety.ppt_tasks 表（P3 管理，勿改表结构）。

设计（PRD P01–P08）：主题输入 → AI 生成大纲(outline JSON) → 用户编辑 → 选模板 → 生成 → file_path 落盘 → 导出。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class PptTask(Base):
    __tablename__ = "ppt_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    topic: Mapped[str] = mapped_column(String(256))
    outline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 大纲 JSON（P01/P02）
    template: Mapped[str] = mapped_column(String(32), default="business")  # 商务/简约/科技（P04）
    file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # 生成的 .pptx 路径
    status: Mapped[str] = mapped_column(
        Enum("草稿", "生成中", "已完成", "失败"), default="草稿"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PptTask id={self.id} topic={self.topic!r} status={self.status!r}>"
