"""AI 问答路由（A01–A04）+ v2.0 大点 01「回答提速」编排。

A01/A03：POST /api/chat —— SSE 流式问答（StreamingResponse，media_type="text/event-stream"）
- 请求：{question, conversation_id?, title?, high_precision?}
- 响应：data: {"meta": {...}}（可选，前端可忽略）
        data: {"delta": "..."} 逐块
        data: [DONE]
- 注意：SSE 生成器内用独立 SessionLocal（get_db 的依赖会话会随响应关闭）

提速编排（见 v2.0 规划文档 §01 ③ 与 Verdict/01）：
1. 会话首问 / 缓存过期 → 全量检索；
2. 追问 → 先判定「会话已有材料够不够」，够就直接生成，不够**只补缺口**；
3. Redis 不可用 → 只凭会话上下文（最近 3 轮 + 上轮已引用来源）判断缺口，仍不整题重检；
4. 预处理（陈述句化 / 专业化 / 剔除已检索）在所有路径都做；多主题拆分由 Agent 决定；
5. 上下文按 RAG_CONTEXT_BUDGET_CHARS 预算裁剪（改造前单问 10 万~20 万字符）；
6. 重排精度按请求的 high_precision 切换（默认 fp16）。
"""
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.ai import chat_agent, intent, query_preprocess, sufficiency
from src.ai.llm_client import llm_client
from src.ai.prompts import render
from src.ai.rag import format_context, retrieve_queries
from src.ai.reranker import reranker
from src.core.config import settings
from src.core.db import SessionLocal, get_db
from src.core.security import get_current_user
from src.schemas.chat import (
    ChatRequest,
    ConversationOut,
    MessageOut,
    QuickQuestionOut,
    RatingRequest,
)
from src.services import conv_rag_cache
from src.services import preprocess_audit as preprocess_audit_service
from src.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI 问答"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _block_key(block: dict) -> str:
    """子块去重键：缓存块用 chunk_id，新检索块用文本哈希。"""
    from src.services.conv_rag_cache import chunk_id

    return block.get("chunk_id") or chunk_id(block.get("text") or "")


def _merge_sources(base: list[dict], extra: list[dict]) -> list[dict]:
    """合并来源（doc+章节去重，保持原有顺序）：Agent 补检命中也要能点开。"""
    merged: list[dict] = []
    seen: set[tuple] = set()
    for item in (base or []) + (extra or []):
        key = (
            item.get("doc", ""),
            item.get("chapter", "") or item.get("article_no", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


#: 检索失败时给模型的说明（裁定 5）：不伪造来源，只如实告知「本轮没有资料」
RETRIEVAL_DEGRADED_NOTE = (
    "（本轮知识库检索失败，没有可用的参考资料。请仅依据通用安全常识谨慎作答，"
    "并在回答开头用一句话说明：本次未能检索企业知识库，回答不含出处。）"
)

#: 闲聊直答注入的最近对话行数上限（3 轮 = user+assistant 各 3 条）
CHAT_HISTORY_LINES = 6


def _safe_retrieve(queries: list[str]) -> tuple[list[dict], dict | None]:
    """检索兜底（裁定 5）：任何异常都降级为空块 + 降级标记，绝不让整轮回答 500。"""
    try:
        return retrieve_queries(queries), None
    except Exception as exc:  # noqa: BLE001 —— 向量库/盘/网络异常都走这里
        logger.warning("检索失败，本轮降级为无资料回答: %s", exc)
        return [], {"kind": "retrieval", "why": type(exc).__name__, "detail": str(exc)[:120]}


def _update_user_refs(message_id: int, payload: dict) -> None:
    """回填 user 消息的 refs（预处理 / 意图判定）；失败不影响回答。"""
    try:
        db = SessionLocal()
        try:
            ChatService.update_message_refs(db, message_id, json.dumps(payload, ensure_ascii=False))
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("user refs 回填失败: %s", exc)


def _save_assistant(conv_id: int, answer: str, refs: str | None) -> None:
    """回答落库（独立 SessionLocal：SSE 期间依赖会话已关闭）。"""
    if not answer:
        return
    db = SessionLocal()
    try:
        ChatService.save_message(db, conv_id, "assistant", answer, refs=refs)
    finally:
        db.close()


def _trim_current(history: str, question: str) -> str:
    """从 history 里去掉「本轮问题」那行，只留之前几轮（闲聊直答用最近 3 轮）。"""
    lines = [ln for ln in (history or "").splitlines() if ln.strip()]
    q = (question or "").strip()
    if lines and q and lines[-1].startswith("用户:") and q[:20] in lines[-1]:
        lines = lines[:-1]
    return "\n".join(lines[-CHAT_HISTORY_LINES:])


def _prepare(conv_id: int, question: str, history: str, is_first: bool = False) -> dict:
    """跑预处理 + 判定 + 必要的检索，返回装配上下文所需的全部材料。

    返回：{mode, context, sources, new_blocks, pre, judge, degraded, timings}
    mode ∈ {full, cache, incremental, context}

    is_first=True 表示本会话的首问（或缓存已过期后重置的一问）——此时**必须全量检索**，
    不进入判定分支；退化路径（无 Redis）同样遵守该规则。
    """
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    degraded = not conv_rag_cache.available()
    cached = None if degraded else conv_rag_cache.get(conv_id)

    # 「已检索摘要」来源：正常模式用 Redis 块摘要；退化模式用 DB 里上轮答案的引用
    cited: list[str] = []
    if degraded:
        db = SessionLocal()
        try:
            digest = ChatService.context_digest(db, conv_id)
            cited = digest["cited_docs"]
            history = history or digest["history"]
        finally:
            db.close()
    covered = [] if degraded else conv_rag_cache.summary(conv_id)

    # 1) 预处理（所有路径必做）
    pre = query_preprocess.preprocess(question, history=history, covered=covered)
    timings["preprocess"] = time.perf_counter() - t0

    t1 = time.perf_counter()
    new_blocks: list[dict] = []
    cached_blocks: list[dict] = []
    judge: dict | None = None
    retr_degrade: dict | None = None

    if cached and cached.get("blocks"):
        cached_blocks = [
            {
                "text": b.get("text", ""),
                "metadata": b.get("metadata") or {},
                "rerank_score": b.get("rerank_score"),
                "chunk_id": b.get("chunk_id"),
            }
            for b in cached.get("blocks", [])
        ]
        # 2) 追问：判定够不够（输入是块摘要，不是全文）
        judge = sufficiency.judge(
            pre["declarative"], blocks=covered, history=history, degraded=degraded
        )
        timings["judge"] = time.perf_counter() - t1
        if judge["sufficient"]:
            mode = "cache"
        else:
            mode = "incremental"
            t2 = time.perf_counter()
            new_blocks, retr_degrade = _safe_retrieve(judge["queries"])
            timings["retrieve"] = time.perf_counter() - t2
    elif degraded and not is_first:
        # 3) 无 Redis + 有历史：只凭会话上下文判断缺口（裁定 6：不整题重检）
        judge = sufficiency.judge(
            pre["declarative"], blocks=[], cited_docs=cited, history=history, degraded=True
        )
        timings["judge"] = time.perf_counter() - t1
        if judge["sufficient"]:
            mode = "context"
        else:
            mode = "incremental"
            t2 = time.perf_counter()
            new_blocks, retr_degrade = _safe_retrieve(judge["queries"])
            timings["retrieve"] = time.perf_counter() - t2
    else:
        # 4) 首问 / 缓存过期 / 退化模式的首问：全量检索（用预处理的子查询）
        mode = "full"
        t2 = time.perf_counter()
        new_blocks, retr_degrade = _safe_retrieve(pre["subqueries"])
        timings["retrieve"] = time.perf_counter() - t2

    # 5) 合并去重（新检索优先）+ 预算裁剪
    merged: dict[str, dict] = {}
    for block in new_blocks + cached_blocks:
        merged.setdefault(_block_key(block), block)
    blocks = list(merged.values())
    context, sources = format_context(blocks) if blocks else ("", [])
    timings["context"] = time.perf_counter() - t1

    return {
        "mode": mode,
        "context": context,
        "sources": sources,
        "new_blocks": new_blocks,
        "pre": pre,
        "judge": judge,
        "degraded": degraded,
        "retrieval_degraded": retr_degrade,
        "timings": timings,
    }


@router.post("/api/chat", summary="A01/A03 智能问答（SSE 流式）")
def chat(data: ChatRequest, user: dict = Depends(get_current_user)):
    # 0) 重排精度：请求级开关（切 fp32 会重载模型，仅当开关真的变化时）
    high_precision = data.high_precision or settings.RAG_HIGH_PRECISION_DEFAULT
    reranker.set_high_precision(high_precision)

    # 会话与历史（get_db 依赖会话，请求阶段使用）
    db = SessionLocal()
    try:
        conv = ChatService.get_or_create_conversation(db, user["id"], data)
        is_first = not ChatService.has_messages(db, conv.id)  # 首问 → 必须全量检索
        # 意图判定要「上一轮问题 + 上一轮回答开头」——必须在保存本轮问题**之前**取（裁定 2）
        intent_hint = ChatService.last_round_hint(db, conv.id)
        user_msg = ChatService.save_message(db, conv.id, "user", data.question)
        conv_id, user_msg_id = conv.id, user_msg.id
        history = ChatService.build_history(db, conv_id)
    finally:
        db.close()

    def event_stream():
        t_start = time.perf_counter()

        # 0) 意图门禁（v2.0 大点 03 · 裁定 1–4）：闲聊 → 不预处理、不检索，直答
        judged = intent.detect(data.question, hint=intent_hint)
        intent_refs = judged.to_dict()

        if judged.is_chat:
            yield _sse(
                {
                    "meta": {
                        "mode": "chat",
                        "precision": reranker.current_dtype,
                        "degraded": False,
                        "retrieval_degraded": None,
                        "split": False,
                        "queries": [],
                        "context_chars": 0,
                        "retrieval_ms": int((time.perf_counter() - t_start) * 1000),
                        "sources": 0,
                        "intent": intent_refs,
                    }
                }
            )
            chat_out: list[str] = []
            gen_t0 = time.perf_counter()
            try:
                for token in _stream_chat(_trim_current(history, data.question), data.question):
                    chat_out.append(token)
                    yield _sse({"delta": token})
            except Exception as exc:  # noqa: BLE001 —— 闲聊失败也要有明确提示
                logger.warning("闲聊直答失败: %s", exc)
                yield _sse({"error": "回答失败，请稍后重试"})
            gen_ms = int((time.perf_counter() - gen_t0) * 1000)
            _update_user_refs(user_msg_id, {"intent": intent_refs})
            _save_assistant(conv_id, "".join(chat_out), None)
            # 裁定 3：闲聊不写 RAG 缓存（不 put_blocks、不 touch），避免污染后续追问的 covered
            logger.info(
                "闲聊回答 conv=%s intent=%s intent_ms=%d gen_ms=%d（未检索、未写缓存）",
                conv_id,
                judged.label,
                judged.ms,
                gen_ms,
            )
            yield "data: [DONE]\n\n"
            return

        # 1) 知识问答：预处理 + 判定 + 检索（检索段已内置降级）
        try:
            prepared = _prepare(conv_id, data.question, history, is_first=is_first)
        except Exception as exc:  # noqa: BLE001 —— 编排整体失败也不能 500（裁定 5）
            logger.warning("预处理/编排失败，降级为无资料回答: %s", exc)
            prepared = {
                "mode": "full",
                "context": "",
                "sources": [],
                "new_blocks": [],
                "pre": {
                    "declarative": data.question,
                    "subqueries": [data.question],
                    "split": False,
                    "covered_dropped": [],
                    "reason": "编排异常，退回原话检索",
                    "used_llm": False,
                },
                "judge": None,
                "degraded": False,
                "retrieval_degraded": {
                    "kind": "retrieval",
                    "why": type(exc).__name__,
                    "detail": str(exc)[:120],
                },
                "timings": {},
            }
        timings = dict(prepared["timings"])
        timings["prepare_total"] = time.perf_counter() - t_start
        timings["intent"] = judged.ms / 1000

        # 预处理 + 意图判定结果回填到 user 消息（Q5：history 里同时保留原话与检索式）
        _update_user_refs(
            user_msg_id, {"preprocessed": prepared["pre"], "intent": intent_refs}
        )

        # meta 帧（前端可忽略）：模式 / 精度 / 检索耗时 / 上下文体量 / 意图与降级
        yield _sse(
            {
                "meta": {
                    "mode": prepared["mode"],
                    "precision": reranker.current_dtype,
                    "degraded": prepared["degraded"],
                    "retrieval_degraded": prepared.get("retrieval_degraded"),
                    "split": prepared["pre"].get("split", False),
                    "queries": prepared["pre"].get("subqueries", []),
                    "context_chars": len(prepared["context"]),
                    "retrieval_ms": int(timings.get("prepare_total", 0) * 1000),
                    "sources": len(prepared["sources"]),
                    "intent": intent_refs,
                }
            }
        )

        context = prepared["context"]
        retrieval_degraded = prepared.get("retrieval_degraded")
        if retrieval_degraded:
            # 裁定 5：没有资料就如实说明，绝不伪造来源
            context = RETRIEVAL_DEGRADED_NOTE
        sources = list(prepared["sources"])  # 生成过程中可能补入 Agent 补检的来源
        collected: list[str] = []

        # 生成：Agent 优先（可补检一次），异常回落直连 LLM
        if settings.AGENT_ENABLED:
            try:
                user_content = chat_agent.build_user_message(context, history, data.question)
                run_ctx = {"conversation_id": conv_id, "question": data.question,
                           "retrieve_calls": 0, "gap_calls": 0, "new_blocks": []}
                gen_t0 = time.perf_counter()
                for token in chat_agent.stream_answer(user_content, run_ctx):
                    collected.append(token)
                    yield _sse({"delta": token})
                timings["generate"] = time.perf_counter() - gen_t0
                extra = run_ctx.get("new_blocks") or []
                if extra:
                    prepared["new_blocks"].extend(extra)
                    # 补检命中的块也要进 refs，否则回答里引用了却点不开（来源标签缺失）
                    _extra_ctx, extra_sources = format_context(extra)
                    sources = _merge_sources(sources, extra_sources)
                    logger.info(
                        "Agent 补检 %d 块（conv=%s，新增来源 %d 条）",
                        len(extra),
                        conv_id,
                        len(extra_sources),
                    )
            except Exception as exc:  # noqa: BLE001 —— Agent 不可用就退回普通流式
                logger.warning("Agent 生成失败，回落直连 LLM: %s", exc)
                collected = list(_stream_direct(context, history, data.question, timings))
                for token in collected:
                    yield _sse({"delta": token})
        else:
            collected = list(_stream_direct(context, history, data.question, timings))
            for token in collected:
                yield _sse({"delta": token})

        # 落库 + 缓存续期（生成期间不计时）+ TTL 语义在回答完成后开始
        answer = "".join(collected)
        refs_json = json.dumps(sources, ensure_ascii=False) if sources else None
        save_db = SessionLocal()
        try:
            if answer:
                ChatService.save_message(save_db, conv_id, "assistant", answer, refs=refs_json)
            if prepared["new_blocks"]:
                conv_rag_cache.put_blocks(
                    conv_id,
                    prepared["new_blocks"],
                    queries=prepared["pre"].get("subqueries"),
                    rewrite={"original": data.question, "preprocessed": prepared["pre"]},
                )
            conv_rag_cache.touch(conv_id)
        finally:
            save_db.close()

        # v2.0 大点 04：资料确实缺时，异步让 Agent 上网补（不阻塞 SSE，裁定 10）
        try:
            from src.ai import agent_scout

            gap_by_tool = bool((run_ctx or {}).get("gap_report")) if settings.AGENT_ENABLED else False
            if not gap_by_tool and agent_scout.should_scout(prepared, len(sources), answer):
                agent_scout.kick_off(conv_id, data.question)
                logger.info(
                    "已触发 Agent 采集（conv=%s，来源 %d 条，答案自述缺口=%s）",
                    conv_id, len(sources), agent_scout.answer_declares_gap(answer),
                )
        except Exception as exc:  # noqa: BLE001 —— 触发失败不影响本轮回答
            logger.debug("触发 Agent 采集失败: %s", exc)

        logger.info(
            "问答完成 conv=%s mode=%s intent=%s precision=%s%s %s",
            conv_id,
            prepared["mode"],
            judged.label,
            reranker.current_dtype,
            "（检索降级）" if retrieval_degraded else "",
            " ".join(f"{k}={v*1000:.0f}ms" for k, v in timings.items()),
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _stream_chat(history: str, question: str):
    """闲聊直答（裁定 3）：身份/能力/边界提示 + 最近几轮，**不注入知识库资料**。"""
    prompt = render("chat_assistant", history=history or "（无）")
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": question},
    ]
    yield from llm_client.chat_stream(messages, temperature=0.5)


def _stream_direct(context: str, history: str, question: str, timings: dict):
    """直连 LLM 的流式生成（Agent 关闭或失败时的兜底；含 agnes 降级链）。"""
    system_prompt = render(
        "safety_qa",
        retrieved_context=context or "（无）",
        history=history or "（无）",
        question=question,
    )
    messages = [{"role": "system", "content": system_prompt}]
    t0 = time.perf_counter()
    try:
        for chunk in llm_client.chat_stream(messages, temperature=0.3):
            yield chunk
    finally:
        timings["generate"] = time.perf_counter() - t0


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
    conv_rag_cache.clear(conversation_id)  # 会话删了，缓存也一并清掉
    return {"message": "会话已删除"}


@router.get(
    "/api/conversations/{conversation_id}/preprocess-audit",
    summary="A08 预处理审计（该会话每轮：原问题 → 检索式/子查询/拆分/剔除）",
)
def preprocess_audit(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """只读：把该会话每轮的「原问题 → 预处理结果」返回给前端审计按钮。

    与 `scripts/show_preprocessed.py` 共用 `preprocess_audit.collect()`，
    保证命令行与页面看到的是同一份数据。
    """
    from src.models.conversation import Conversation  # 局部导入，避免循环

    conv = db.get(Conversation, conversation_id)
    if not conv or conv.user_id != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或不属于当前用户"
        )
    return preprocess_audit_service.collect(conversation_id, db=db)


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
