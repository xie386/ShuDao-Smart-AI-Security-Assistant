"""站内消息（v2.0 大点 07 重写 · 裁定 2/3/8/9）。

从「每用户一个 Redis list」改成 **HASH（消息体）+ ZSET（时序）**，因为 list 做不到：
- 单条 TTL（有的消息闭环即删、有的留 1 天、有的允许堆积）；
- 按 `hazard_id + type` 精准删除（闭环时清上报/整改/派单）；
- 已读状态（裁定 8：进页面即已读，角标 = 真未读数）。

键：
    shudao:notify:<uid>      HASH  msg_id -> JSON
    shudao:notify:z:<uid>    ZSET  msg_id -> created_at(score)

消息体：
    {id, type, message, hazard_id, contrib_id, created_at, expire_at, read, status}
    - expire_at 为 None 表示"不自动过期"，只能靠业务删除；
    - accept 消息用 status 区分 rejected（堆积）/ passed（留 1 天）。

过期策略：**读/写时惰性清理**（不依赖 Redis 7.4 的 HEXPIRE，外部 Redis 可能是 5.x）。
单用户消息上限 `NOTIFY_MAX_PER_USER`（默认 500），超出淘汰最旧，防止无界增长。
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from src.core.config import settings
from src.core.redis_client import redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "shudao:notify:"
_Z_PREFIX = "shudao:notify:z:"

# 消息类型（前端按 type 分类渲染）
TYPE_REPORT = "report"          # 接收上报 → admin
TYPE_ASSIGN = "assign"          # 整改任务 → 责任人
TYPE_RECTIFY = "rectify"        # 整改反馈 → admin
TYPE_ACCEPT = "accept"          # 验收结果 → 被派单的 safety_officer
TYPE_FINAL = "final"            # 最终处理 → 上报人 + admin
TYPE_KB_CONTRIB = "kb_contrib"  # 知识贡献 → admin + 贡献者
TYPE_GENERAL = "general"

STATUS_REJECTED = "rejected"
STATUS_PASSED = "passed"

DEFAULT_TTL_DAY = 86400
_DOWN_UNTIL = 0.0          # 熔断：Redis 连不上后 30s 内不再尝试（避免 109 个收件人逐个超时）


def _down() -> bool:
    return time.time() < _DOWN_UNTIL


def _trip(exc: Exception) -> None:
    """连接类错误 → 熔断 30s；其它错误（如 WRONGTYPE）不熔断。"""
    global _DOWN_UNTIL
    if exc.__class__.__name__ in ("ConnectionError", "TimeoutError", "BusyLoadingError"):
        _DOWN_UNTIL = time.time() + 30


def _key(user_id: int) -> str:
    return f"{_KEY_PREFIX}{user_id}"


def _zkey(user_id: int) -> str:
    return f"{_Z_PREFIX}{user_id}"


def _ttl(ttl: int | None) -> int | None:
    """None = 不自动过期；传 0/负数按"当天"兜底，避免误传 0 造成永不删除。"""
    if ttl is None:
        return None
    return int(ttl) if int(ttl) > 0 else DEFAULT_TTL_DAY


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        # 兼容 v2.0 大点 07 之前的纯字符串旧消息
        return {
            "id": uuid.uuid4().hex[:12], "type": TYPE_GENERAL, "message": str(raw),
            "hazard_id": None, "contrib_id": None, "created_at": int(time.time()),
            "expire_at": None, "read": False, "status": None,
        }
    if not isinstance(obj, dict):
        return None
    obj.setdefault("id", uuid.uuid4().hex[:12])
    obj.setdefault("type", TYPE_GENERAL)
    obj.setdefault("message", "")
    obj.setdefault("hazard_id", None)
    obj.setdefault("contrib_id", None)
    obj.setdefault("created_at", int(time.time()))
    obj.setdefault("expire_at", None)
    obj.setdefault("read", False)
    obj.setdefault("status", None)
    return obj


def _is_expired(msg: dict, now: int | None = None) -> bool:
    exp = msg.get("expire_at")
    return bool(exp) and int(exp) <= (now or int(time.time()))


def _max_per_user() -> int:
    return int(getattr(settings, "NOTIFY_MAX_PER_USER", 500) or 500)


# --------------------------------------------------------------------------- #
# 内部：清理与修剪
# --------------------------------------------------------------------------- #
def _sweep(user_id: int, limit: int = 200) -> int:
    """清掉过期的消息（最多扫 limit 条，避免一次读爆）。返回删除条数。"""
    if _down():
        return 0
    zkey, hkey = _zkey(user_id), _key(user_id)
    try:
        ids = redis_client.zrange(zkey, 0, limit - 1)
        if not ids:
            return 0
        raws = redis_client.hmget(hkey, ids)
        dead = []
        now = int(time.time())
        for mid, raw in zip(ids, raws):
            msg = _parse(raw)
            if msg is None or _is_expired(msg, now):
                dead.append(mid)
        if dead:
            redis_client.hdel(hkey, *dead)
            redis_client.zrem(zkey, *dead)
        return len(dead)
    except Exception as exc:  # noqa: BLE001 —— Redis 不可用时全部降级
        _trip(exc)
        logger.warning("消息清理失败(user=%s): %s", user_id, exc)
        return 0


def _trim(user_id: int) -> None:
    """单用户消息超上限时淘汰最旧的（按时间序）。"""
    zkey, hkey = _zkey(user_id), _key(user_id)
    try:
        total = int(redis_client.zcard(zkey) or 0)
        cap = _max_per_user()
        if total <= cap:
            return
        old = redis_client.zrange(zkey, 0, total - cap - 1)
        if old:
            redis_client.hdel(hkey, *old)
            redis_client.zrem(zkey, *old)
            logger.info("消息超上限：user=%s 淘汰 %d 条（上限 %d）", user_id, len(old), cap)
    except Exception as exc:  # noqa: BLE001
        logger.warning("消息修剪失败(user=%s): %s", user_id, exc)


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
def push(
    user_id: int | None,
    message: str,
    hazard_id: int | None = None,
    msg_type: str = TYPE_GENERAL,
    *,
    contrib_id: int | None = None,
    ttl: int | None = None,
    status: str | None = None,
) -> str | None:
    """推送一条消息，返回 msg_id（失败返回 None，不抛异常）。

    Args:
        ttl: 秒；None = 不自动过期（等业务删除）。
        status: accept 消息用 rejected/passed 区分。
    """
    if user_id is None or _down():
        # 旧代码在集合运算里可能带出 None（research §S2-5），这里硬拦，避免造孤儿键
        if user_id is None:
            logger.warning("消息收件人为空，已丢弃：%s", message[:40])
        return None
    now_ts = time.time()          # ZSET 用浮点秒做 score：同秒内也能保持正确先后
    now = int(now_ts)
    mid = uuid.uuid4().hex[:12]
    payload = {
        "id": mid,
        "type": msg_type,
        "message": message,
        "hazard_id": hazard_id,
        "contrib_id": contrib_id,
        "created_at": now,
        "expire_at": (now + _ttl(ttl)) if _ttl(ttl) else None,
        "read": False,
        "status": status,
    }
    try:
        _sweep(user_id)
        pipe = redis_client.pipeline()
        pipe.hset(_key(user_id), mid, _dump(payload))
        pipe.zadd(_zkey(user_id), {mid: now_ts})
        pipe.execute()
        _trim(user_id)
        return mid
    except Exception as exc:  # noqa: BLE001
        _trip(exc)
        logger.warning("消息推送失败(user=%s): %s", user_id, exc)
        return None


def mark_read(user_id: int, ids: list[str] | None = None, all_: bool = False) -> int:
    """标记已读，返回剩余未读数。ids 为空且 all_=True 时全部标已读。"""
    hkey = _key(user_id)
    try:
        if _down():
            return 0
        _sweep(user_id)
        if all_ or not ids:
            targets = redis_client.zrange(_zkey(user_id), 0, -1)
        else:
            targets = [i for i in ids if i]
        if not targets:
            return unread_count(user_id)
        raws = redis_client.hmget(hkey, targets)
        pipe = redis_client.pipeline()
        changed = 0
        for mid, raw in zip(targets, raws):
            msg = _parse(raw)
            if not msg or msg.get("read"):
                continue
            msg["read"] = True
            pipe.hset(hkey, mid, _dump(msg))
            changed += 1
        if changed:
            pipe.execute()
        return unread_count(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("标记已读失败(user=%s): %s", user_id, exc)
        return 0


def drop(
    user_id: int,
    *,
    hazard_id: int | None = None,
    contrib_id: int | None = None,
    types: list[str] | None = None,
    status: str | None = None,
) -> int:
    """按条件删某用户的消息（闭环清上报/整改/派单、删堆积的驳回）。返回删除数。"""
    hkey, zkey = _key(user_id), _zkey(user_id)
    try:
        if _down():
            return 0
        _sweep(user_id)
        ids = redis_client.zrange(zkey, 0, -1)
        if not ids:
            return 0
        raws = redis_client.hmget(hkey, ids)
        dead = []
        for mid, raw in zip(ids, raws):
            msg = _parse(raw)
            if not msg:
                dead.append(mid)
                continue
            if types and msg.get("type") not in types:
                continue
            if status and msg.get("status") != status:
                continue
            if hazard_id is not None and msg.get("hazard_id") != hazard_id:
                continue
            if contrib_id is not None and msg.get("contrib_id") != contrib_id:
                continue
            if hazard_id is None and contrib_id is None and not types:
                continue  # 什么都不限就不要误删
            dead.append(mid)
        if dead:
            redis_client.hdel(hkey, *dead)
            redis_client.zrem(zkey, *dead)
        if not redis_client.zcard(zkey):
            redis_client.delete(hkey, zkey)
        return len(dead)
    except Exception as exc:  # noqa: BLE001
        logger.warning("消息删除失败(user=%s): %s", user_id, exc)
        return 0


def drop_for_users(users, **kwargs) -> int:
    """对一批收件人执行同一个删除条件（闭环时用）。"""
    total = 0
    for uid in {u for u in (users or []) if u is not None}:
        total += drop(uid, **kwargs)
    return total


def clear(user_id: int) -> bool:
    try:
        if _down():
            return False
        redis_client.delete(_key(user_id), _zkey(user_id))
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def list_for(user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    """最新 → 最旧。返回消息 dict 列表（已剔除过期项）。"""
    hkey, zkey = _key(user_id), _zkey(user_id)
    try:
        if _down():
            return []
        _sweep(user_id)
        ids = redis_client.zrevrange(zkey, offset, offset + max(0, limit) - 1)
        if not ids:
            return []
        raws = redis_client.hmget(hkey, ids)
        now = int(time.time())
        out, dead = [], []
        for mid, raw in zip(ids, raws):
            msg = _parse(raw)
            if msg is None or _is_expired(msg, now):
                if raw is not None:
                    dead.append(mid)
                continue
            out.append(msg)
        if dead:
            redis_client.hdel(hkey, *dead)
            redis_client.zrem(zkey, *dead)
        return out
    except Exception as exc:  # noqa: BLE001 —— Redis 挂了返回空，不 500
        logger.warning("消息读取失败(user=%s): %s", user_id, exc)
        return []


def total_count(user_id: int) -> int:
    try:
        if _down():
            return 0
        _sweep(user_id)
        return int(redis_client.zcard(_zkey(user_id)) or 0)
    except Exception:  # noqa: BLE001
        return 0


def unread_count(user_id: int) -> int:
    hkey, zkey = _key(user_id), _zkey(user_id)
    try:
        if _down():
            return 0
        _sweep(user_id)
        ids = redis_client.zrange(zkey, 0, -1)
        if not ids:
            return 0
        raws = redis_client.hmget(hkey, ids)
        return sum(1 for raw in raws if (m := _parse(raw)) and not m.get("read"))
    except Exception:  # noqa: BLE001
        return 0


def count(user_id: int) -> dict:
    """角标用：{unread, total, latest:{message,type,created_at}}。"""
    try:
        latest = list_for(user_id, limit=1)
        return {
            "unread": unread_count(user_id),
            "total": total_count(user_id),
            "latest": (
                {k: latest[0].get(k) for k in ("id", "type", "message", "created_at", "hazard_id", "contrib_id")}
                if latest else None
            ),
        }
    except Exception:  # noqa: BLE001
        return {"unread": 0, "total": 0, "latest": None}


# --------------------------------------------------------------------------- #
# 兼容旧调用（v2.0 大点 07 之前直接 `NotificationService.push(...)`）
# --------------------------------------------------------------------------- #
class NotificationService:
    push = staticmethod(push)
    list_for = staticmethod(list_for)
    count = staticmethod(unread_count)
    clear = staticmethod(clear)
    mark_read = staticmethod(mark_read)
    drop = staticmethod(drop)
    drop_for_users = staticmethod(drop_for_users)
