"""非人类用户（Agent）与采集运行记录（v2.0 大点 04 · 裁定 7/10）。

两张表都不改 `users`：
- `agent_clients`：Agent 身份（名称、API Key 哈希、模型、归属人、权限范围、启用位）；
- `agent_runs`   ：每次网搜采集的运行记录（缺口问题、访问过的 URL、产物 md、贡献 id、状态）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class AgentClient(Base):
    __tablename__ = "agent_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    api_key_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    owner_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scopes: Mapped[str] = mapped_column(String(255), default="contrib:submit")
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentClient id={self.id} name={self.name!r} enabled={self.enabled}>"


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gap_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    methodology_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    result_md_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contrib_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AgentRun id={self.id} status={self.status!r} conv={self.conversation_id}>"
