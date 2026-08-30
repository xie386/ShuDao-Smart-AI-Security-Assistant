"""H08 消息通知（站内消息，Redis 队列）。
 
设计：
- 每条通知存 JSON 字符串 {"message": str, "hazard_id": int|None, "type": str}，
  hazard_id 供前端点击跳转隐患详情；type 供前端分类展示（assign 派发/rectify 整改反馈/accept 验收/final 最终处理）；
- 兼容旧数据：纯字符串旧消息解析失败时按 {"message": raw, "hazard_id": None, "type": "general"} 处理；
- Redis 未启动时 push/list/count/clear 全部优雅降级（不抛错）。
"""
import json
import logging

from src.core.redis_client import redis_client

logger = logging.getLogger(__name__)
 
_KEY_PREFIX = "shudao:notify:"

# 消息类型（前端按 type 分类渲染）
TYPE_REPORT = "report"      # 接收上报 → 管理员（有人上报新隐患）
TYPE_ASSIGN = "assign"      # 派发整改任务 → 责任人
TYPE_RECTIFY = "rectify"    # 整改反馈 → 上报人 + 管理员
TYPE_ACCEPT = "accept"      # 验收结果 → 责任人/上报人 + 安全员/管理员
TYPE_FINAL = "final"        # 最终处理结果 → 上报人/责任人/管理员/安全员
TYPE_KB_CONTRIB = "kb_contrib"  # 知识贡献：提交待审→管理员；审核结果→上传者
TYPE_GENERAL = "general"    # 兜底


def _key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{user_id}"


def _dump(message: str, hazard_id: int | None, msg_type: str) -> str:
    return json.dumps({"message": message, "hazard_id": hazard_id, "type": msg_type}, ensure_ascii=False)
 
 
def _parse(raw: str) -> dict:
    """解析通知条目：新格式 dict / 旧格式纯字符串 / 损坏数据。"""
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and "message" in obj:
            obj.setdefault("type", TYPE_GENERAL)
            return obj
        return {"message": raw, "hazard_id": None, "type": TYPE_GENERAL}
    except (ValueError, TypeError):
        return {"message": raw, "hazard_id": None, "type": TYPE_GENERAL}


class NotificationService:
    @staticmethod
    def push(user_id: int, message: str, hazard_id: int | None = None, msg_type: str = TYPE_GENERAL) -> bool:
        """推送通知（hazard_id 关联隐患供跳转，msg_type 供前端分类）。

        知识贡献联动：hazard_id 传 None 时前端按 msg_type=kb_contrib 展示；
        跳转贡献详情由消息文本内嵌贡献 #id 实现（避免复用 hazard_id 语义混乱）。
        """
        try:
            redis_client.rpush(_key(user_id), _dump(message, hazard_id, msg_type))
            return True
        except Exception as exc:  # Redis 未启动等
            logger.warning("通知推送失败(user=%s): %s", user_id, exc)
            return False

    @staticmethod 
    def list_for(user_id: int, limit: int = 50) -> list[dict]:
        try:
            #lrange 从队尾取 limit 条，-limit 表示从队尾取 limit 条
            raws = redis_client.lrange(_key(user_id), -limit, -1)
        except Exception as exc:
            logger.warning("通知读取失败(user=%s): %s", user_id, exc)
            return []
        return [_parse(r) for r in raws]

    @staticmethod
    def count(user_id: int) -> int:
        try:
            return int(redis_client.llen(_key(user_id)) or 0)
        except Exception:
            return 0

    @staticmethod
    def clear(user_id: int) -> bool:
        try:
            return bool(redis_client.delete(_key(user_id)))
        except Exception:
            return False
