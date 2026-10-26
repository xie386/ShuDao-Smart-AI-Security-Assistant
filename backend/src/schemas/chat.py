"""AI 问答与会话契约（A01–A04）。"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """A01 问答请求。conversation_id 为空则自动新建会话。"""

    question: str = Field(min_length=1, max_length=2000, description="用户问题")
    conversation_id: Optional[int] = Field(default=None, description="会话 ID（空=新建）")
    title: Optional[str] = Field(default=None, max_length=128, description="新建会话标题（默认取问题前 20 字）")
    high_precision: bool = Field(
        default=False,
        description="高精度检索（v2.0 大点 01）：True 时本轮重排走 fp32（更准但更慢）",
    )


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: Optional[str] = None
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    refs: Optional[str] = None
    rating: Optional[int] = None
    created_at: datetime


class QuickQuestionOut(BaseModel):
    """A04 快捷提问条目。"""

    label: str
    question: str


class ChatResponse(BaseModel):
    """非流式响应（stream=false 时）。"""

    conversation_id: int
    answer: str
 

class RatingRequest(BaseModel):
    """A07 点赞/踩：rating=1 点赞，-1 踩，0 取消。"""

    rating: int = Field(ge=-1, le=1)
