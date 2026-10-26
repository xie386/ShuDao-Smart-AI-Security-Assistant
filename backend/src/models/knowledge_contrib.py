"""知识贡献模型：对齐 shudao_safety.knowledge_contributions / knowledge_contrib_timeline 表。

业务（知识贡献模块实现计划书）：全员可上传 → admin 审核 → 审核通过才调 upsert_chunks
入向量库（增量追加，不重建）。状态机：待审核 → 已入库 / 已驳回。
updated_at 无 ON UPDATE 触发器，由 SQLAlchemy onupdate 应用层维护（同 hazards）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class KnowledgeContribution(Base):
    """知识贡献主表（K01 提交 → K06 审核）。"""

    __tablename__ = "knowledge_contributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(MEDIUMTEXT)  # 文件解析文本（可能超 64KB，用 mediumtext）
    file_intro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # 文件简介（上传者编写）
    file_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # 上传文件相对路径
    doc_type: Mapped[str] = mapped_column(
        Enum("laws", "procedures", "plans", "cases"), default="laws"
    )
    contributor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(
        Enum("待审核", "已入库", "已驳回"), default="待审核"
    )
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    review_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    chunk_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # ---- v2.0 大点 04：来源与落盘信息 ----
    source_kind: Mapped[str] = mapped_column(
        Enum("user", "agent"), default="user", server_default="user"
    )
    source_agent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    origin_urls: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON 数组
    origin_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    md_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    convert_by: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeContribution id={self.id} title={self.title!r} status={self.status!r}>"


class KnowledgeContribTimeline(Base):
    """贡献进度时间线（K03 详情展示，对齐 hazard_timeline）。"""

    __tablename__ = "knowledge_contrib_timeline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contrib_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_contributions.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(64))  # 提交/审核通过/审核驳回
    operator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="human", server_default="human")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeContribTimeline id={self.id} contrib={self.contrib_id} action={self.action!r}>"
