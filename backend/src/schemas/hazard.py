"""隐患模块契约（H01–H06）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

LEVELS = ("重大", "较大", "一般", "轻微")


class HazardCreate(BaseModel):
    """H01 隐患上报请求。"""

    title: str = Field(min_length=2, max_length=128, description="隐患标题")
    description: str = Field(min_length=5, max_length=2000, description="隐患描述")
    level: str = Field(pattern="^(重大|较大|一般|轻微)$", description="隐患等级")
    hazard_type: Optional[str] = Field(default=None, max_length=64, description="隐患类型")
    location: Optional[str] = Field(default=None, max_length=128, description="位置")
    image_urls: Optional[list[str]] = Field(default=None, description="上传接口返回的图片 url 列表")


class AssignRequest(BaseModel):
    """H04 派单请求。"""

    assignee_id: int = Field(description="责任人用户 ID")
    due_date: Optional[datetime] = Field(default=None, description="整改期限")


class RectifyRequest(BaseModel):
    """H05 整改反馈请求。"""

    rectify_measures: str = Field(min_length=5, max_length=2000, description="整改措施")
    rectify_images: Optional[list[str]] = Field(default=None, description="整改后照片 url 列表")


class AcceptRequest(BaseModel):
    """H06 验收请求。"""

    approved: bool = Field(description="True=通过（已闭环） False=驳回（已驳回）")
    note: Optional[str] = Field(default=None, max_length=500, description="验收意见")


class TimelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    operator_id: Optional[int] = None
    note: Optional[str] = None
    created_at: datetime


class HazardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    level: str
    hazard_type: Optional[str] = None
    location: Optional[str] = None
    status: str
    reporter_id: int
    assignee_id: Optional[int] = None
    due_date: Optional[datetime] = None
    image_urls: Optional[str] = None
    rectify_measures: Optional[str] = None
    rectify_images: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class HazardDetailOut(HazardOut):
    """H03 详情：隐患 + 处理进度时间线。"""

    timeline: list[TimelineOut] = []

 
class HazardListOut(BaseModel):
    """H02 列表响应（分页）。"""

    total: int
    page: int
    page_size: int
    items: list[HazardOut]
