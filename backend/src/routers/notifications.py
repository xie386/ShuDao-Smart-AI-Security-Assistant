"""消息接口（v2.0 大点 07 · 裁定 8/11）。

- `GET  /api/notifications`        列表（新→旧，真 total + 真 unread）
- `GET  /api/notifications/count`  轻量角标（30s 轮询用）
- `POST /api/notifications/read`   标已读（进页面自动调用）
- `DELETE /api/notifications`      清空我的消息
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src.core.security import get_current_user
from src.schemas.common import MessageResponse
from src.services import notification_service as notify

router = APIRouter(prefix="/api/notifications", tags=["消息通知"])


class ReadRequest(BaseModel):
    ids: list[str] = Field(default_factory=list, description="要标已读的消息 id")
    all: bool = Field(default=False, description="True=全部标已读")


@router.get("", summary="我的消息列表（H08 · 新→旧）")
def my_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    items = notify.list_for(user["id"], limit=limit, offset=offset)
    return {
        "total": notify.total_count(user["id"]),
        "unread": notify.unread_count(user["id"]),
        "items": items,
    }


@router.get("/count", summary="未读数（轻量，30s 轮询用）")
def notification_count(user: dict = Depends(get_current_user)):
    return notify.count(user["id"])


@router.post("/read", summary="标记已读")
def mark_read(body: ReadRequest, user: dict = Depends(get_current_user)):
    unread = notify.mark_read(user["id"], ids=body.ids, all_=body.all)
    return {"unread": unread}


@router.delete("", response_model=MessageResponse, summary="清空我的消息")
def clear_notifications(user: dict = Depends(get_current_user)):
    notify.clear(user["id"])
    return MessageResponse(message="消息已清空")
