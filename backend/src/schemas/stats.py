"""H07 统计分析契约。"""
from typing import Optional

from pydantic import BaseModel


class TrendPoint(BaseModel):
    date: str
    created: int
    closed: int = 0
    """
    注释文档:
    TrendPoint作用：统计趋势点，包含日期、创建数、关闭数。
    """


class HazardStatsOut(BaseModel):
    total: int
    by_level: dict[str, int] = {}
    by_type: dict[str, int] = {}
    rectify_rate: float = 0.0  # 0~1
    pending: int = 0  # 待处理/处理中 未闭环数
    trend: list[TrendPoint] = []
    """
    注释文档:
    HazardStatsOut作用：隐患统计分析响应，包含总数、按等级/类型分组、整改率、待处理数、趋势点。
    """
