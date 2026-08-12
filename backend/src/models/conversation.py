"""AI 会话模型：对齐 shudao_safety.conversations / chat_messages 表（P3 管理，勿改表结构）。

设计（AI_SOLUTION §3.2）：会话历史存 MySQL，多轮拼接 {history} 实现 ≥5 轮上下文；
chat_messages.references 存回答引用的知识来源（A06）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Conversation id={self.id} user={self.user_id} title={self.title!r}>"


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant / system
    content: Mapped[str] = mapped_column(Text)
    # 列名 references 为 SQL 保留字语境，ORM 属性用 refs 规避
    refs: Mapped[Optional[str]] = mapped_column("references", Text, nullable=True)  # 引用来源 JSON
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # A07 点赞(1)/踩(-1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatMessage id={self.id} conv={self.conversation_id} role={self.role!r}>"
