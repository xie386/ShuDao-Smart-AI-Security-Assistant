"""记录表：已闭环隐患 + 知识贡献结果（v2.0 大点 07 · 裁定 5/6）。

为什么单独一张表、只存"已结束"的事：
- 消息在 Redis 会过期/被删，而"办完的事"要能长期翻阅；
- 隐患一旦闭环，它的流转细节（谁上报、谁整改、驳回几次、验收意见、AI 建议）就固定了，
  存快照比每次联表拼装更稳，也便于以后导出/审计；
- 知识贡献的审核结果同样需要一个"贡献者能回看"的落点。

`kind` 区分两类记录；`ref_id` 是隐患/贡献 id；`UNIQUE(kind, ref_id)` 保证幂等
（同一隐患多次验收、同一贡献重复审，都只留一条）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base

KIND_HAZARD = "hazard_closed"
KIND_CONTRIB = "kb_contrib"


class MessageRecord(Base):
    __tablename__ = "message_records"
    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uk_kind_ref"),
        Index("ix_records_kind_time", "kind", "created_at"),
        Index("ix_records_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: hazard_closed / kb_contrib
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: hazard_id / contribution_id
    ref_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 可见性归属：隐患=上报人；贡献=贡献者（Agent 贡献挂 owner/兜底 admin）
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    #: 已闭环 / 已入库 / 已驳回
    status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MessageRecord {self.kind}#{self.ref_id}>"
