"""会话级子块缓存（v2.0 大点 01）。

为什么按会话、按子块：
- 旧实现是 `md5(question)` 精确匹配（chat.py 原 _qa_cache_*），真实对话里几乎不可能命中；
- 本实现把「这次会话已经检索到什么」按 **子块** 存起来：追问时可直接复用、只补缺口；
- 不存父块：父块 p50 = 117,312 字符，存进去会把 Redis 和上下文一起顶爆（见 research §4.3）；
  只存「父块中围绕该子块的窗口」，体量与预算同量级（默认 4 块 × 4000 字符）。

TTL 语义（对应草案 3）：
- 不在检索完成时计时，而是**回答完成后**由 `touch()` 刷新为 600s；
- 追问命中缓存同样 `touch()`，即「10 分钟没有继续问才清」；
- 生成答案耗时不计入计时。

Redis 不可用：所有方法静默失败（返回 None / False），调用方据此走「上下文回顾式退化」。
"""
import hashlib
import json
import logging
import time

from src.core.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "shudao:rag:conv:"
_MAX_BLOCKS = 12          # 单会话最多缓存块数（防无限膨胀）
_SUMMARY_HEAD = 200       # 判定器摘要视图里每块保留的字数


def _key(conversation_id: int | str) -> str:
    return f"{_PREFIX}{conversation_id}"


def _client():
    """懒取 Redis 客户端（Redis 未启动时该模块本身可用，只是调用失败）。"""
    from src.core.redis_client import redis_client  # lazy

    return redis_client


def chunk_id(text: str) -> str:
    """子块稳定标识（文本哈希），用于跨轮去重。"""
    return hashlib.md5((text or "").strip().encode("utf-8")).hexdigest()[:16]


def normalize_block(hit: dict, per_block_budget: int | None = None) -> dict:
    """把检索命中转成缓存块：只留上下文窗口 + 精简元数据，不存整个父块。"""
    from src.ai.rag import _cropped_block_text  # lazy，避免循环 import

    meta = hit.get("metadata") or {}
    budget = per_block_budget or max(800, settings.RAG_CONTEXT_BUDGET_CHARS // 4)
    return {
        "chunk_id": chunk_id(hit.get("text") or ""),
        "text": _cropped_block_text(hit, budget),
        "rerank_score": hit.get("rerank_score"),
        "metadata": {
            "doc": meta.get("doc", ""),
            "doc_type": meta.get("doc_type", ""),
            "chapter": meta.get("chapter", ""),
            "article_no": meta.get("article_no", ""),
            "source": meta.get("source", ""),
        },
    }


def get(conversation_id: int | str) -> dict | None:
    """读会话缓存；无缓存 / Redis 异常 / 格式不符 → None。"""
    if not settings.RAG_CONV_CACHE_ENABLED:
        return None
    try:
        raw = _client().get(_key(conversation_id))
        if not raw:
            return None
        obj = json.loads(raw)
        if not isinstance(obj, dict) or not isinstance(obj.get("blocks"), list):
            return None
        return obj
    except Exception as exc:  # noqa: BLE001 —— Redis 不可用等
        logger.debug("会话缓存读取失败(conv=%s): %s", conversation_id, exc)
        return None


def put_blocks(
    conversation_id: int | str,
    hits: list[dict],
    queries: list[str] | None = None,
    rewrite: dict | None = None,
) -> bool:
    """把本轮命中的子块并入会话缓存（按 chunk_id 去重、按重排分保留高分、限量）。

    返回是否写入成功（Redis 异常 → False，不影响主链路）。
    """
    if not settings.RAG_CONV_CACHE_ENABLED or not hits:
        return False
    try:
        obj = get(conversation_id) or {"blocks": [], "queries": [], "rewrites": [], "updated": 0}
        pool: dict[str, dict] = {b["chunk_id"]: b for b in obj.get("blocks", []) if b.get("chunk_id")}

        for hit in hits:
            block = normalize_block(hit)
            cid = block["chunk_id"]
            if not cid:
                continue
            old = pool.get(cid)
            if old is None or (block.get("rerank_score") or 0) > (old.get("rerank_score") or 0):
                pool[cid] = block

        blocks = sorted(
            pool.values(), key=lambda b: b.get("rerank_score") or 0, reverse=True
        )[:_MAX_BLOCKS]
        obj["blocks"] = blocks

        known = list(obj.get("queries") or [])
        for q in queries or []:
            if q and q not in known:
                known.append(q)
        obj["queries"] = known[-20:]
        if rewrite:
            obj.setdefault("rewrites", []).append(rewrite)
            obj["rewrites"] = obj["rewrites"][-10:]
        obj["updated"] = int(time.time())

        _client().setex(
            _key(conversation_id),
            settings.RAG_CONV_CACHE_TTL,
            json.dumps(obj, ensure_ascii=False),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("会话缓存写入失败(conv=%s): %s", conversation_id, exc)
        return False


def touch(conversation_id: int | str) -> bool:
    """回答完成后刷新 TTL（生成期间不计时的落点）。"""
    if not settings.RAG_CONV_CACHE_ENABLED:
        return False
    try:
        return bool(_client().expire(_key(conversation_id), settings.RAG_CONV_CACHE_TTL))
    except Exception as exc:  # noqa: BLE001
        logger.debug("会话缓存续期失败(conv=%s): %s", conversation_id, exc)
        return False


def summary(conversation_id: int | str, max_chars: int = _SUMMARY_HEAD) -> list[dict]:
    """判定器用的块摘要视图（不是全文）：doc#chapter + 每块前 N 字。"""
    obj = get(conversation_id)
    if not obj:
        return []
    out: list[dict] = []
    for b in obj.get("blocks", []):
        meta = b.get("metadata") or {}
        head = (b.get("text") or "")[:max_chars].replace("\n", " ")
        out.append(
            {
                "doc": meta.get("doc", ""),
                "chapter": meta.get("chapter", "") or meta.get("article_no", ""),
                "head": head,
            }
        )
    return out


def clear(conversation_id: int | str) -> bool:
    """清空某会话的缓存（调试/测试用；会话删除时也可调用）。"""
    try:
        return bool(_client().delete(_key(conversation_id)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("会话缓存清理失败(conv=%s): %s", conversation_id, exc)
        return False


def available() -> bool:
    """Redis 是否可用（决定走正常模式还是「上下文回顾式退化」）。"""
    if not settings.RAG_CONV_CACHE_ENABLED:
        return False
    try:
        return bool(_client().ping())
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis 不可用: %s", exc)
        return False
