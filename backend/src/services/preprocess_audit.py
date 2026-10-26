"""预处理审计（v2.0 大点 01 · A08）：把会话里每轮「原问题 → 预处理结果」整理出来。

数据来源：`chat_messages.references`（ORM 属性 `refs`，列名是 SQL 保留字 `references`）。
每条 **user** 消息写入 `{"preprocessed": {...}, "intent": {...}}`（见 `routers/chat.py` 的回填逻辑）：
  preprocessed：declarative / subqueries / split / covered_dropped / reason / used_llm
  intent（v2.0 大点 03）：label / reason / ms / model / degraded
闲聊轮**只有 intent、没有 preprocessed**（跳过预处理与检索），审计里要照常展示。

两个消费方共用本模块，保证「命令行看到的」与「前端看到的」完全一致：
- CLI：`scripts/show_preprocessed.py`
- HTTP：`GET /api/conversations/{conversation_id}/preprocess-audit`
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.db import SessionLocal
from src.models.conversation import ChatMessage

logger = logging.getLogger(__name__)

_PRE_FIELDS = ("declarative", "subqueries", "split", "covered_dropped", "reason", "used_llm")
_INTENT_FIELDS = ("label", "reason", "ms", "model", "degraded")


def _loads(raw: str | None, default):
    if not raw:
        return default
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default
    return obj if isinstance(obj, type(default)) else default


def parse_preprocessed(refs: str | None) -> dict | None:
    """从 user 消息的 refs 里取 preprocessed；没有（01 之前的消息）返回 None。"""
    obj = _loads(refs, {})
    pre = obj.get("preprocessed") if isinstance(obj, dict) else None
    if not isinstance(pre, dict):
        return None
    out = {k: pre.get(k) for k in _PRE_FIELDS}
    out["subqueries"] = out["subqueries"] if isinstance(out["subqueries"], list) else []
    out["covered_dropped"] = (
        out["covered_dropped"] if isinstance(out["covered_dropped"], list) else []
    )
    out["split"] = bool(out["split"])
    out["used_llm"] = bool(out["used_llm"])
    return out


def parse_intent(refs: str | None) -> dict | None:
    """从 user 消息的 refs 里取 intent 判定（v2.0 大点 03）；01 及更早的消息返回 None。"""
    obj = _loads(refs, {})
    it = obj.get("intent") if isinstance(obj, dict) else None
    if not isinstance(it, dict):
        return None
    out = {k: it.get(k) for k in _INTENT_FIELDS}
    label = out.get("label")
    out["label"] = label if label in ("chat", "knowledge") else "knowledge"
    out["reason"] = out.get("reason") or ""
    try:
        out["ms"] = int(out.get("ms") or 0)
    except (TypeError, ValueError):
        out["ms"] = 0
    out["model"] = out.get("model") or ""
    degraded = out.get("degraded")
    out["degraded"] = degraded if isinstance(degraded, dict) else None
    return out


def _sources_count(refs: str | None) -> int:
    obj = _loads(refs, [])
    return len(obj) if isinstance(obj, list) else 0


def build_rounds(messages: list[ChatMessage]) -> list[dict]:
    """按「用户问 → 助手答」配对成轮次（纯函数，便于单测）。

    尾部只有用户消息（正在生成 / 生成失败）时也返回一轮，`assistant_message_id=None`，
    这样前端在出错时点审计也能看到预处理结果。
    """
    rounds: list[dict] = []
    pending: dict | None = None
    for m in messages:
        if m.role == "user":
            if pending is not None:  # 上一条用户消息没有回答（异常中断），也保留
                rounds.append({**pending, "assistant_message_id": None, "sources": 0, "rating": None})
            pending = {
                "user_message_id": m.id,
                "question": m.content or "",
                "preprocessed": parse_preprocessed(m.refs),
                "intent": parse_intent(m.refs),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
        elif m.role == "assistant" and pending is not None:
            rounds.append(
                {
                    **pending,
                    "assistant_message_id": m.id,
                    "sources": _sources_count(m.refs),
                    "rating": m.rating,
                }
            )
            pending = None
    if pending is not None:
        rounds.append({**pending, "assistant_message_id": None, "sources": 0, "rating": None})
    return rounds


def collect(conversation_id: int, db: Session | None = None) -> dict:
    """取某会话的全部轮次预处理信息（调用方负责归属校验）。"""
    own = db is None
    session = db or SessionLocal()
    try:
        msgs = session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id)
        ).all()
        rounds = build_rounds(list(msgs))
        with_pre = sum(1 for r in rounds if r["preprocessed"])
        with_intent = sum(1 for r in rounds if r["intent"])
        chat_rounds = sum(1 for r in rounds if (r.get("intent") or {}).get("label") == "chat")
        return {
            "conversation_id": conversation_id,
            "total_rounds": len(rounds),
            "rounds_with_preprocess": with_pre,
            "rounds_with_intent": with_intent,
            "chat_rounds": chat_rounds,
            "rounds": rounds,
        }
    finally:
        if own:
            session.close()
