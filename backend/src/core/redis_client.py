"""Redis 连接池（D2 起用于会话缓存 / 接口限流 / 站内消息队列）。

设计： 
- lazy：连接池在首次 import 时创建，但 redis-py 是懒连接（真正连接在首次命令时）；
- 服务未启动时调用会抛 ConnectionError，可用 ping_redis() 优雅探测；
- 当前本机 6379 未启动，D2 使用前需启动 Redis（.env 的 REDIS_URL 可配）。
- 文件命名为 redis_client.py 而非 redis.py：避免与第三方 redis 库同名冲突（直接运行时脚本目录会遮蔽库）。
"""
import logging
import sys
from functools import lru_cache
from pathlib import Path

# 允许直接运行（如 python src/core/redis_client.py）时找到 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import redis
from src.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache
def get_redis() -> redis.Redis:
    """创建 Redis 客户端（decode_responses=True 直接拿 str，省去手动 decode）。"""
    # protocol=2：redis-py 8.x 默认 RESP3 会发 HELLO 命令，老版本 Redis(<6.0) 不支持报
    # "unknown command `HELLO`"，强制 RESP2 兼容（本机 Redis 即此类场景）。
    client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, protocol=2)
    logger.info("Redis 客户端已创建: %s", settings.REDIS_URL)
    return client


def ping_redis() -> bool:
    """探测 Redis 可用性：服务未启动返回 False（不抛异常）。"""
    try:
        return bool(get_redis().ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用: %s", exc)
        return False


redis_client = get_redis()

if __name__ == "__main__":
    print(ping_redis())
