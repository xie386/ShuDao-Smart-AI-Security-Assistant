"""H08 消息通知路由（站内消息，Redis 队列）。"""
from fastapi import APIRouter, Depends, Query
 
from src.core.security import get_current_user
from src.schemas.common import MessageResponse 
from src.services.notification_service import NotificationService

router = APIRouter(prefix="/api/notifications", tags=["消息通知"])

 
@router.get("", summary="我的通知列表（H08）")
def my_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    items = NotificationService.list_for(user["id"], limit=limit)
    return {"total": len(items), "items": items, "unread": NotificationService.count(user["id"])}


@router.delete("", response_model=MessageResponse, summary="清空我的通知")
def clear_notifications(user: dict = Depends(get_current_user)):
    NotificationService.clear(user["id"])
    return MessageResponse(message="通知已清空")
