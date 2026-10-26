"""问题预处理（v2.0 大点 01）。

职责：把用户问题转成**检索友好**的表述，并标出「会话里已覆盖、不用再检索」的部分。

四类动作（见 `prompts.yml::query_preprocess`）：
- a) 陈述句化   —— 必做
- b) 语义专业化 —— 必做（口语 → 书文/规范用词，可多角度）
- c) 多主题拆分 ——**只是建议**，最终由 Agent 决定（拆分=多次检索=时长翻倍；
                   判据：同实体不拆、不同实体才拆，最多 AGENT_MAX_SPLIT_QUERIES 条）
- d) 剔除已检索 —— 必做（无可剔除则跳过）

与 Redis 无关：不管缓存是否可用，a/b 都要做；d 的「已检索摘要」来源有两处
（Redis 块摘要 或 DB 里上轮答案的 refs），由调用方传入 `covered`，本模块不区分来源。
"""
import json
import logging
import re

from src.core.config import settings

logger = logging.getLogger(__name__)

# 规则快路径：明显已是陈述句、短、无指代词、且会话里没有可剔除项 → 省掉一次 LLM 往返
_INTERROGATIVES = ("吗", "呢", "什么", "如何", "怎么", "怎样", "哪些", "哪个", "多少", "是否", "为什么", "？", "?")
_ANAPHORA = ("它", "这个", "那个", "这些", "那些", "上述", "前面", "刚才", "该条", "此项")


def _parse_json(content: str) -> dict:
    """宽容解析：剥 ``` 围栏 → 取首 { 到末 } → json.loads。失败返回 {}。"""
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


def _dedup(items: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for it in items:
        s = (it or "").strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _looks_declarative(question: str) -> bool:
    return not any(k in question for k in _INTERROGATIVES) and not any(
        k in question for k in _ANAPHORA
    )


def _format_covered(covered: list[dict] | None) -> str:
    """把「已检索摘要」拼成提示词里的一段（doc#章节 + 首 120 字）。"""
    if not covered:
        return "（无）"
    lines = []
    for item in covered[:8]:
        head = (item.get("head") or "")[:120].replace("\n", " ")
        label = item.get("doc", "")
        if item.get("chapter"):
            label += f"#{item['chapter']}"
        lines.append(f"- {label}：{head}")
    return "\n".join(lines)


def _fallback(question: str, reason: str) -> dict:
    """LLM 不可用时的兜底：不动句式（a/b 用原话），c 走零 LLM 关键词切分，d 跳过。"""
    try:
        from src.ai.rag import _keyword_split  # lazy

        queries = _dedup(_keyword_split(question), settings.AGENT_MAX_SPLIT_QUERIES)
    except Exception:  # noqa: BLE001
        queries = []
    if not queries:
        queries = [question]
    return {
        "declarative": question,
        "subqueries": queries,
        "split": len(queries) > 1,
        "covered_dropped": [],
        "reason": reason,
        "used_llm": False,
    }


def preprocess(
    question: str, history: str = "", covered: list[dict] | None = None
) -> dict:
    """预处理入口。

    参数：
    - question：用户原话
    - history：最近几轮对话文本（用于补全省略的主语/上下文）
    - covered：本会话已检索到的摘要视图（Redis 块摘要 或 DB refs 转换而来）
    返回：{declarative, subqueries, split, covered_dropped, reason, used_llm}
    """
    q = (question or "").strip()
    if not q:
        return {"declarative": "", "subqueries": [], "split": False,
                "covered_dropped": [], "reason": "空问题", "used_llm": False}

    if _looks_declarative(q) and len(q) <= 30 and not covered:
        return {"declarative": q, "subqueries": [q], "split": False,
                "covered_dropped": [], "reason": "已是陈述句且短、无指代、无可剔除项（规则快路径）",
                "used_llm": False}

    try:
        from src.ai.llm_client import llm_client  # lazy
        from src.ai.prompts import render  # lazy

        prompt = render(
            "query_preprocess",
            question=q,
            history=(history or "（无）"),
            covered=_format_covered(covered),
        )
        content = llm_client.chat([{"role": "system", "content": prompt}], temperature=0.0)
        data = _parse_json(content)
        if not data:
            return _fallback(q, "预处理返回非 JSON，退回原话检索")

        declarative = (data.get("declarative") or q).strip() or q
        subs = data.get("subqueries") or []
        if not isinstance(subs, list):
            subs = []
        subs = _dedup([str(s) for s in subs], settings.AGENT_MAX_SPLIT_QUERIES)

        split = bool(data.get("split"))
        if not split or len(subs) <= 1:
            subs = [declarative]           # 不拆：只保留主检索式
            split = False
        dropped = data.get("covered_dropped") or []
        return {
            "declarative": declarative,
            "subqueries": subs,
            "split": split,
            "covered_dropped": [str(x) for x in dropped][:5] if isinstance(dropped, list) else [],
            "reason": str(data.get("reason") or "")[:200],
            "used_llm": True,
        }
    except Exception as exc:  # noqa: BLE001 —— 预处理失败不能挡住回答
        logger.warning("问题预处理失败(%s)，退回原话: %s", q[:20], exc)
        return _fallback(q, f"预处理异常({type(exc).__name__})，退回原话检索")
