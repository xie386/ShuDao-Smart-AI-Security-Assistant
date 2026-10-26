"""检索充分性判定（v2.0 大点 01）。

职责：判断「本会话已有材料」够不够回答当前问题；不够时给出**缺口子查询**，
让编排层只补缺口、而不是把整题重新检索一遍。

两套输入（同一套判定提示词，见 `prompts.yml::sufficiency_judge`）：
- 正常模式（Redis 可用）：会话缓存块的摘要视图（doc#章节 + 首 200 字）
- 退化模式（Redis 不可用）：`chat_service.context_digest()` —— 最近 3 轮问答摘要
  + 上一轮答案 `refs` 里已引用的 doc#章节（纯 DB 读取）

判失败/异常一律**保守**：判为「不够」，但只针对当前问题做一次检索（不整题重检历史）。
"""
import json
import logging
import re

from src.core.config import settings

logger = logging.getLogger(__name__)


def _parse_json(content: str) -> dict:
    text = (content or "").strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _format_material(blocks: list[dict] | None, cited_docs: list[str] | None) -> str:
    """把「已有材料」拼成一段：有块摘要用块摘要；没有就只列已引用的文档。"""
    lines: list[str] = []
    for b in (blocks or [])[:10]:
        label = b.get("doc", "")
        if b.get("chapter"):
            label += f"#{b['chapter']}"
        head = (b.get("head") or "")[:200].replace("\n", " ")
        lines.append(f"- {label}：{head}")
    for d in (cited_docs or [])[:10]:
        line = f"- 已引用：{d}"
        if line not in lines:
            lines.append(line)
    return "\n".join(lines) if lines else "（本会话尚无已检索内容）"


def _conservative(question: str, reason: str) -> dict:
    return {
        "sufficient": False,
        "missing": [],
        "queries": [question] if question else [],
        "reason": reason,
        "used_llm": False,
    }


def judge(
    question: str,
    blocks: list[dict] | None = None,
    cited_docs: list[str] | None = None,
    history: str = "",
    degraded: bool = False,
) -> dict:
    """判定已有材料是否足够。

    参数：
    - question：预处理后的检索式（declarative）
    - blocks：正常模式的块摘要视图（`conv_rag_cache.summary()`）
    - cited_docs：退化模式下从上轮 `refs` 取到的 doc#章节 列表
    - degraded：Redis 不可用（仅影响日志与提示，不影响判定逻辑）
    返回：{sufficient, missing, queries, reason, used_llm}
    """
    q = (question or "").strip()
    if not q:
        return _conservative(q, "空问题")

    material = _format_material(blocks, cited_docs)
    try:
        from src.ai.llm_client import llm_client  # lazy
        from src.ai.prompts import render  # lazy

        prompt = render(
            "sufficiency_judge",
            question=q,
            material=material,
            history=(history or "（无）"),
        )
        content = llm_client.chat([{"role": "system", "content": prompt}], temperature=0.0)
        data = _parse_json(content)
        if not data:
            return _conservative(q, "判定返回非 JSON，按不够处理")

        sufficient = bool(data.get("sufficient"))
        queries = data.get("queries") or []
        if not isinstance(queries, list):
            queries = []
        queries = [str(x).strip() for x in queries if str(x).strip()][
            : settings.AGENT_MAX_SPLIT_QUERIES
        ]
        if sufficient:
            queries = []
        elif not queries:
            queries = [q]           # 判不够但没给缺口 → 只重检当前问题
        return {
            "sufficient": sufficient,
            "missing": [str(x)[:80] for x in (data.get("missing") or [])][:5]
            if isinstance(data.get("missing"), list) else [],
            "queries": queries,
            "reason": str(data.get("reason") or "")[:200],
            "used_llm": True,
        }
    except Exception as exc:  # noqa: BLE001 —— 判定失败不能挡住回答
        logger.warning("充分性判定失败(降级=%s): %s", degraded, exc)
        return _conservative(q, f"判定异常({type(exc).__name__})，按不够处理")
