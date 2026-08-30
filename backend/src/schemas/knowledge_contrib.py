"""知识贡献模块契约（K01–K06）。

K01 为 multipart 上传（form 字段 + file），不走 pydantic body，见 routers/knowledge_contrib.py。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeContribOut(BaseModel):
    """贡献记录（列表/详情）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    file_intro: Optional[str] = None
    file_path: Optional[str] = None
    doc_type: str
    contributor_id: int
    status: str  # 待审核 / 已入库 / 已驳回
    review_note: Optional[str] = None
    review_by: Optional[int] = None
    chunk_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class KnowledgeContribListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[KnowledgeContribOut]


class ReviewRequest(BaseModel):
    """K06 审核请求（仅 admin）。"""

    approved: bool = Field(..., description="true=通过入库 / false=驳回")
    note: Optional[str] = Field(default=None, max_length=500, description="审核意见")


class ReviewPreviewOut(BaseModel):
    """K05 审核预筛结果（子智能体辅助，不写库）。"""

    in_scope: Optional[bool] = None
    suggestion: str  # 通过 / 驳回 / 复核
    reason: str = ""
    matched: list[str] = []
    doc_type: str = ""


class ReviewStandardOut(BaseModel):
    """判定标准文档（管理员审批界面查看入口）。"""

    filename: str
    content: str
