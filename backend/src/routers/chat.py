"""AI 问答路由（A01–A04）。

A01/A03：POST /api/chat —— SSE 流式问答（StreamingResponse，media_type="text/event-stream"）。
- 请求：{question, conversation_id?, title?}
- 响应：data: {"delta": "..."}\n\n 逐块推送，结束 data: [DONE]\n\n
- 流程：取/建会话 → 存 user 消息 → 拼历史 → 渲染 safety_qa → llm_client.chat_stream → 流结束存 assistant
- 注意：SSE 生成器内用独立 SessionLocal（get_db 的依赖会话会随响应关闭）
"""
import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.ai.llm_client import llm_client
from src.ai.prompts import render
from src.ai.rag import hybrid_retrieve
from src.core.db import SessionLocal, get_db
from src.core.security import get_current_user
from src.schemas.chat import (
    ChatRequest,
    ConversationOut,
    MessageOut,
    QuickQuestionOut,
    RatingRequest,
)
from src.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI 问答"])

# ---- 领导优化：RAG 检索分块缓存（Redis，优雅降级） ----
# 需求（2026-08-17）：不缓存最终答案（保证回答多样性——LLM 每次重新生成），
# 而是缓存「答案依据的检索分块结果」：context（父块拼接文本）+ sources（引用元数据）。
# 相同问题命中 → 跳过 RAG 检索，直接用缓存分块作为 LLM 上下文重新生成回答。
# TTL 10 分钟（领导要求：分块缓存时限 10 分钟，防知识库更新后上下文长期过期）。
# key: shudao:rag_cache:<md5(question)>，value: JSON {"context": str, "sources": [...]}
# Redis 未启动时 get/set 全部静默跳过，不影响主链路。
_QA_CACHE_PREFIX = "shudao:rag_cache:"
_QA_CACHE_TTL = 10 * 60  # 10 分钟（领导要求）


def _qa_cache_key(question: str) -> str:
    import hashlib

    return f"{_QA_CACHE_PREFIX}{hashlib.md5(question.strip().encode('utf-8')).hexdigest()}"


def _qa_cache_get(key: str) -> tuple[str, list[dict]] | None:
    """读缓存分块：返回 (context, sources)；无缓存/格式不符/Redis 异常 → None。"""
    try:
        from src.core.redis_client import redis_client  # lazy

        val = redis_client.get(key)
        if not val:
            return None
        obj = json.loads(val)
        if isinstance(obj, dict) and isinstance(obj.get("context"), str):
            sources = obj.get("sources") or []
            return obj["context"], sources
        return None  # 旧格式（纯字符串答案）→ 视为未命中，重新检索
    except Exception as exc:  # Redis 未启动 / JSON 损坏等
        logger.debug("RAG 分块缓存读取失败: %s", exc)
        return None


def _qa_cache_set(key: str, context: str, sources: list[dict]) -> None:
    """写缓存分块：JSON 序列化 context + sources，TTL 10 分钟。"""
    if not context.strip():
        return
    try:
        from src.core.redis_client import redis_client  # lazy

        redis_client.setex(
            key,
            _QA_CACHE_TTL,
            json.dumps({"context": context, "sources": sources}, ensure_ascii=False),
        )
    except Exception as exc:  # Redis 未启动等
        logger.debug("RAG 分块缓存写入失败: %s", exc)


@router.post("/api/chat", summary="A01/A03 智能问答（SSE 流式）")
def chat(data: ChatRequest, user: dict = Depends(get_current_user)):
    # 领导优化：RAG 分块缓存——命中跳过检索（10 分钟内同问题复用分块），
    # 但 LLM 每次都重新生成（回答多样性）；引用始终保留。
    cache_key = _qa_cache_key(data.question)
    cached = _qa_cache_get(cache_key)  # (context, sources) | None

    # 会话与历史（get_db 依赖会话，请求阶段使用）
    db = SessionLocal()
    try:
        conv = ChatService.get_or_create_conversation(db, user["id"], data)
        ChatService.save_message(db, conv.id, "user", data.question)
        history = ChatService.build_history(db, conv.id)
    finally:
        db.close()

    def event_stream():
        # 领导优化：RAG 分块缓存——命中跳过检索（10 分钟内同问题复用分块），
        # 但 LLM 每次都重新生成（回答多样性）；引用始终保留。
        # 惰性：RAG 检索在流开始时执行（含重模型加载），缓存读取在此处同样惰性，
        # 保证测试可 monkeypatch chat_stream 时不会真实触发检索。
        if cached is None:
            context, sources = hybrid_retrieve(data.question)
            _qa_cache_set(cache_key, context, sources)
        else:
            context, sources = cached
            logger.info("RAG 分块缓存命中: %s", data.question[:30])

        # 渲染 safety_qa（retrieved_context 由 RAG/缓存分块填充）
        system_prompt = render(
            "safety_qa",
            retrieved_context=context,
            history=history,
            question=data.question,
        )
        messages = [{"role": "system", "content": system_prompt}]
        # 引用元数据（A05/A06）：缓存分块同样带 sources → 命中也有引用文件
        refs_json = json.dumps(sources, ensure_ascii=False) if sources else None

        collected: list[str] = []
        try:
            for chunk in llm_client.chat_stream(messages, temperature=0.3):
                collected.append(chunk)
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.error("问答流式失败: %s", exc)
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            # assistant 消息落库（独立会话，SSE 响应后 get_db 会话已关闭）
            answer = "".join(collected)
            if answer:
                save_db = SessionLocal()
                try:
                    ChatService.save_message(
                        save_db, conv.id, "assistant", answer, refs=refs_json
                    )
                finally:
                    save_db.close()
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/api/conversations", response_model=list[ConversationOut], summary="A02 我的会话列表")
def list_conversations(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return ChatService.list_conversations(db, user["id"])


@router.delete("/api/conversations/{conversation_id}", summary="A02 删除会话（含消息）")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    ChatService.delete_conversation(db, user["id"], conversation_id)
    return {"message": "会话已删除"}


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=list[MessageOut],
    summary="A02 会话消息历史",
)
def conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return ChatService.list_messages(db, user["id"], conversation_id)


@router.get(
    "/api/chat/quick-questions",
    response_model=list[QuickQuestionOut],
    summary="A04 快捷提问",
)
def quick_questions(user: dict = Depends(get_current_user)):
    return ChatService.quick_questions()


@router.post(
    "/api/messages/{message_id}/rating",
    summary="A07 点赞/踩（1 赞 / -1 踩 / 0 取消）",
)
def rate_message(
    message_id: int,
    data: RatingRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    msg = ChatService.rate_message(db, user["id"], message_id, data.rating)
    return {"message": "已记录反馈", "rating": msg.rating}
