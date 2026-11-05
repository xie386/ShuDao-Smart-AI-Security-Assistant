"""隐患 AI 识别分析留档（v2.0 大点 06 · 裁定 5）。

为什么不塞进 `hazards.image_urls`：那里只能放 URL，**坐标与建议依据**无处存；
也不能给 `hazards` 加列（「不改主表」铁律）。因此单开一张表，一隐患一条（可多版本）。

字段：
- `boxes_json`：视觉/Agent 结果（含 location / level / category / suggestion / refs / grounded），
  前端据此做 SVG 叠加与筛选复核，后续也能做训练集；
- `raw_json`：模型原始返回（排查用）；
- `annotated_url`：服务端烘焙的标注图（JPG），供列表缩略图 / 详情 / 下载。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base


class HazardAiAnalysis(Base):
    __tablename__ = "hazard_ai_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hazard_id: Mapped[int] = mapped_column(Integer, ForeignKey("hazards.id"), index=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    boxes_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    annotated_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    #: uploader / agent / admin —— 谁触发的这次分析
    generated_by: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HazardAiAnalysis id={self.id} hazard={self.hazard_id}>"
