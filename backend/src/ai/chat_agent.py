"""AI 助手 Agent 化（v2.0 大点 01）。

定位：把「一次问答」交给 LangChain 1.x 的 `create_agent` 执行，模型可以在资料不足时
主动调用 `retrieve_more` 补检一次；不调工具时就是普通生成。

三条设计约束（见 `Verdict/01-AI助手模块升级.md` 裁定 2 / 裁定 8 / 裁定 9）：
1. **延迟护栏**：整轮最多一次工具调用（工具内部计数，确定性拒绝，不靠模型自觉）；
   拆分上限 `AGENT_MAX_SPLIT_QUERIES` 条（拆分=多次检索=时长翻倍）。
2. **降级**：Agent 构建/调用异常 → 调用方回落 `llm_client.chat_stream`（含 agnes 降级链）。
3. **不使用 checkpointer**：多轮上下文由 MySQL 的 `build_history()` 每轮注入
   （最近 5 轮），避免 agent 内存与 DB 双份上下文互相污染、也避免 MemorySaver
   在重启/多 worker 下丢状态的问题。

用法（由 `routers/chat.py` 调用）：
    ctx = {"conversation_id": cid, "calls": 0, "new_blocks": []}
    for token in stream_answer(user_content, ctx):
        yield token
    new_blocks = ctx["new_blocks"]      # 工具补检得到的块，交给会话缓存
"""
import logging
from functools import lru_cache
from typing import Iterator

from src.core.config import settings

logger = logging.getLogger(__name__)

# 每次流式调用的运行上下文，通过 **LangChain config** 传给工具（不走 contextvars）。
# 为什么不能用 contextvars：Starlette 迭代同步生成器时每次 next() 都在线程池的
# 新 context 副本里执行，set() 与 reset() 跨上下文 → 抛
# "<Token ...> was created in a different Context"，Agent 路径会整体失败回落。
# config 是随 runnable 显式传递的参数，工具在任意线程里都能读到同一份对象。
_CTX_KEY = "rag_run_ctx"


def _current_run_ctx() -> dict | None:
    """工具里取「本轮运行上下文」（会话 id / 补检计数 / 新命中块）。"""
    try:
        from langchain_core.runnables.config import ensure_config

        cfg = ensure_config().get("configurable") or {}
        return cfg.get(_CTX_KEY)
    except Exception:  # noqa: BLE001 —— 取不到就当没有（工具自行跳过补检）
        return None


def _make_tools():
    """构造 Agent 工具集（延迟 import，避免无谓加载）。"""
    from langchain.tools import tool

    @tool
    def retrieve_more(queries: str) -> str:
        """补充检索缺失信息。多个子查询用英文分号 ; 分隔，最多 2 条。

        只在【参考资料】明显不足以回答当前问题时调用；整轮最多一次。
        同一实体的多个方面请合并成一条；不同实体才拆开。
        """
        ctx = _current_run_ctx()
        if ctx is None:
            return "（没有可用的检索上下文，跳过补检）"
        if ctx.get("retrieve_calls", 0) >= 1:
            return "（本轮补检次数已用完，请基于现有【参考资料】回答，并说明无法确认的部分）"
        ctx["retrieve_calls"] = ctx.get("retrieve_calls", 0) + 1

        qs = [q.strip() for q in (queries or "").split(";") if q.strip()]
        qs = qs[: settings.AGENT_MAX_SPLIT_QUERIES]
        if not qs:
            return "（未提供有效子查询）"
        try:
            from src.ai.rag import format_context, retrieve_queries  # lazy

            blocks = retrieve_queries(qs)
            if not blocks:
                return "（补检未命中任何资料）"
            ctx.setdefault("new_blocks", []).extend(blocks)
            text, _sources = format_context(blocks)
            return text or "（补检未命中任何资料）"
        except Exception as exc:  # noqa: BLE001 —— 工具失败不应中断回答
            logger.warning("retrieve_more 失败: %s", exc)
            return f"（补检失败：{type(exc).__name__}，请基于现有资料回答）"

    @tool
    def report_knowledge_gap(reason: str) -> str:
        """当本轮【参考资料】确实没有覆盖用户问题、需要去公开渠道补充知识库时调用。

        调用后系统会在后台采集公开资料、总结成文档并提交**管理员审核**；
        不会立即出现在你的回答里。整轮最多调用一次。
        reason 用一句话说明缺什么（例如"缺少矿洞（地下矿山）作业装备配备要求"）。
        """
        ctx = _current_run_ctx()
        if ctx is None:
            return "（没有可用的运行上下文，无法提交）"
        if ctx.get("gap_calls", 0) >= 1:
            return "（本轮已提交过一次资料补充，请继续用现有资料回答）"
        question = (ctx.get("question") or "").strip()
        if not question:
            return "（缺少问题上下文，无法提交）"
        ctx["gap_calls"] = 1
        try:
            from src.ai import agent_scout  # lazy

            accepted = agent_scout.kick_off(int(ctx.get("conversation_id") or 0), question)
        except Exception as exc:  # noqa: BLE001
            logger.warning("提交资料补充失败: %s", exc)
            return "（提交失败，请基于现有资料回答）"
        ctx["gap_report"] = {"reason": reason, "accepted": accepted}
        return (
            "已受理：后台将按方法论上网采集资料并提交管理员审核"
            if accepted
            else "已受理（相同问题的采集任务已在进行中）"
        )

    return [retrieve_more, report_knowledge_gap]


@lru_cache
def get_agent():
    """构建并缓存 Agent（system_prompt 是静态的，每轮上下文走 user 消息）。"""
    from langchain.agents import create_agent

    from src.ai.llm_client import llm_client
    from src.ai.prompts import get_prompt

    agent = create_agent(
        model=llm_client.primary_chat_model,
        tools=_make_tools(),
        system_prompt=get_prompt("safety_agent")["system_prompt"],
        name="shudao_safety_agent",
    )
    logger.info("AI 助手 Agent 已就绪（工具：retrieve_more，最多 1 次补检）")
    return agent


def build_user_message(retrieved_context: str, history: str, question: str) -> str:
    """本轮 user 消息：参考资料 + 历史 + 问题（模板在 prompts.yml::safety_agent_user）。"""
    from src.ai.prompts import render

    return render(
        "safety_agent_user",
        retrieved_context=retrieved_context or "（无）",
        history=history or "（无）",
        question=question,
    )


def stream_answer(user_content: str, ctx: dict | None = None) -> Iterator[str]:
    """流式生成：yield 增量文本；工具补检的块写回 ctx["new_blocks"]。

    调用方负责异常兜底（Agent 不可用时回落 `llm_client.chat_stream`）。
    ctx 通过 `config.configurable` 传给工具 —— 见模块顶部注释（不能走 contextvars）。
    """
    from langchain_core.messages import AIMessageChunk

    agent = get_agent()
    run_ctx = ctx if ctx is not None else {}
    for chunk, _meta in agent.stream(
        {"messages": [{"role": "user", "content": user_content}]},
        config={"configurable": {_CTX_KEY: run_ctx}},
        stream_mode="messages",
    ):
        if not isinstance(chunk, AIMessageChunk):
            continue
        text = getattr(chunk, "text", "") or ""
        if text:
            yield text
