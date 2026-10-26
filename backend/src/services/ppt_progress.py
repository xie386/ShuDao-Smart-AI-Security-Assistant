"""PPT 进度与任务锁（v2.0 大点 02，裁定 9 / 12）。

为什么不进数据库：`ppt_tasks` 只有 `status` 四态 + `outline`，而 `database/schema.sql`
由 P3 管理、大点 00/01 一贯不动表结构。进度是"几分钟就过期"的瞬时状态，Redis 更合适。

键：
    shudao:ppt:progress:{task_id}   hash  stage/progress/page/page_total/msg/updated/status
    shudao:ppt:lock:{task_id}       抢任务锁（SETNX），防同一任务被两个请求同时生成

Redis 不可用时全部静默降级（返回 None / True），生成流程不受影响 —— 断线恢复能力会退化。
"""
from __future__ import annotations

import json
import logging
import time

from src.core.config import settings
from src.core.redis_client import redis_client

logger = logging.getLogger(__name__)

_PROGRESS = "shudao:ppt:progress:{task_id}"
_LOCK = "shudao:ppt:lock:{task_id}"


def _key(task_id: int) -> str:
    return _PROGRESS.format(task_id=int(task_id))


def set_progress(task_id: int, event: dict, *, status: str | None = None) -> bool:
    """把一条 SSE 事件镜像进 Redis（前端刷新后用 GET 恢复）。"""
    payload = {
        "stage": event.get("stage") or "",
        "progress": int(event.get("progress") or 0),
        "page": int(event.get("page") or 0),
        "page_total": int(event.get("page_total") or 0),
        "msg": str(event.get("msg") or "")[:300],
        "updated": int(time.time()),
        "status": status or ("done" if event.get("done") else "running"),
        "detail": json.dumps(event.get("detail") or {}, ensure_ascii=False)[:600],
    }
    try:
        redis_client.hset(_key(task_id), mapping=payload)
        redis_client.expire(_key(task_id), settings.PPT_PROGRESS_TTL)
        return True
    except Exception as exc:  # noqa: BLE001 —— 进度写失败不影响生成
        logger.debug("写入 PPT 进度失败(task=%s): %s", task_id, exc)
        return False


def get_progress(task_id: int) -> dict | None:
    try:
        data = redis_client.hgetall(_key(task_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 PPT 进度失败(task=%s): %s", task_id, exc)
        return None
    if not data:
        return None
    out: dict = {}
    for k, v in data.items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        val = v.decode() if isinstance(v, bytes) else str(v)
        if key in ("progress", "page", "page_total", "updated"):
            try:
                out[key] = int(val)
            except ValueError:
                out[key] = 0
        else:
            out[key] = val
    return out


def clear_progress(task_id: int) -> None:
    try:
        redis_client.delete(_key(task_id))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# 任务锁（裁定 12：并发守卫）
# --------------------------------------------------------------------------- #
def acquire_lock(task_id: int, ttl: int | None = None) -> bool:
    """抢任务锁；抢不到说明正在生成中（调用方回 409）。Redis 不可用则放行。"""
    try:
        return bool(redis_client.set(
            _LOCK.format(task_id=int(task_id)), str(int(time.time())),
            nx=True, ex=ttl or int(settings.PPT_AGENT_TOTAL_TIMEOUT_S) + 60,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用，任务锁降级放行(task=%s): %s", task_id, exc)
        return True


def release_lock(task_id: int) -> None:
    try:
        redis_client.delete(_LOCK.format(task_id=int(task_id)))
    except Exception:  # noqa: BLE001
        pass


def lock_held(task_id: int) -> bool:
    try:
        return bool(redis_client.exists(_LOCK.format(task_id=int(task_id))))
    except Exception:  # noqa: BLE001
        return False
