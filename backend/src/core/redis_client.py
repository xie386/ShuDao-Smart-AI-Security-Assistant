"""Redis 连接池（D2 起用于会话缓存 / 接口限流 / 站内消息队列）。

设计： 
- lazy：连接池在首次 import 时创建，但 redis-py 是懒连接（真正连接在首次命令时）；
- 服务未启动时调用会抛 ConnectionError，可用 ping_redis() 优雅探测；
- v2.0 大点 05：启动期由 `src/core/redis_boot.py` 保证可用（探测外部 → 拉起内置 → 降级）；
  `redis_client` 改为**惰性代理**，首次命令才建池，这样"运行时覆写 REDIS_URL"才能生效。
- 文件命名为 redis_client.py 而非 redis.py：避免与第三方 redis 库同名冲突（直接运行时脚本目录会遮蔽库）。
""" 
import logging
import sys
import urllib.parse
from functools import lru_cache
"""
注释文档:

"""
from pathlib import Path

# 允许直接运行（如 python src/core/redis_client.py）时找到 src 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import redis
from src.core.config import settings

logger = logging.getLogger(__name__)


def _build(url: str) -> redis.Redis:
    # protocol=2：redis-py 8.x 默认 RESP3 会发 HELLO 命令，老版本 Redis(<6.0) 不支持报
    # "unknown command `HELLO`"，强制 RESP2 兼容。
    return redis.Redis.from_url(url, decode_responses=True, protocol=2)


@lru_cache
def get_redis() -> redis.Redis:
    """创建 Redis 客户端（decode_responses=True 直接拿 str，省去手动 decode）。

    v2.0 大点 05：URL 里带密码、而服务端是内置实例（无密码）时，`AUTH` 会报
    `ERR Client sent AUTH, but no password is set`。这里做一次**自愈**：
    首次连接发现服务端根本没设密码 → 自动改用去掉 auth 的连接串（只在本进程内生效），
    这样不进 `lifespan` 的脚本（探针/离线任务）也能正常用 Redis。
    """
    client = _build(settings.REDIS_URL)
    try:
        client.ping()
    except (redis.exceptions.AuthenticationError, redis.exceptions.ResponseError) as exc:
        msg = str(exc).lower()
        # 只有"服务端压根没设密码"才自愈；密码错（WRONGPASS）必须原样报错，不能掩盖配置问题
        if "without any password" not in msg and "no password is set" not in msg:
            raise
        parts = urllib.parse.urlsplit(settings.REDIS_URL)
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        no_auth = urllib.parse.urlunsplit((parts.scheme or "redis", netloc, parts.path or "/0", "", ""))
        logger.info("服务端未设密码（内置实例），改用无 auth 连接串：%s", no_auth)
        settings.REDIS_URL = no_auth
        client = _build(no_auth)
    logger.info("Redis 客户端已创建: %s", settings.REDIS_URL)
    return client


def ping_redis() -> bool:
    """探测 Redis 可用性：服务未启动返回 False（不抛异常）。"""
    try:
        return bool(get_redis().ping())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 不可用: %s", exc)
        return False
 

def reset() -> None:
    """丢弃已建连接池（Redis URL 被运行时覆写后调用）。"""
    get_redis.cache_clear()


class _LazyRedis:
    """惰性代理：`redis_client.xxx(...)` 与真实客户端用法完全一致。

    为什么要代理：`redis_boot.ensure()` 在 `lifespan` 里可能把 `REDIS_URL` 覆写成无 auth 版本，
    而模块级若直接 `redis_client = get_redis()` 就会在 import 时按旧 URL 建池。
    """

    def __getattr__(self, name):  # 只有真实客户端没有的属性才会走到这里
        return getattr(get_redis(), name)

    def __repr__(self) -> str:  # 日志/调试友好
        return f"<LazyRedis {settings.REDIS_URL}>"


redis_client = _LazyRedis()

if __name__ == "__main__":
    print(ping_redis())
