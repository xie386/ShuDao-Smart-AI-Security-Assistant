#!/usr/bin/env python
"""RAG 评估指标计算（被 scripts/eval_qa.py 调用，也可单独 import）。

六个指标（图示口径）：

| 指标 | 中文 | 是否需要 LLM | 依赖标注 |
|------|------|--------------|----------|
| chunk_recall_at_k    | chunk 级召回 @K   | 否 | expected_docs |
| chunk_precision_at_k | chunk 级精确率 @K | 否 | expected_docs |
| context_recall       | 上下文召回        | 否 | expected_docs |
| answer_correctness   | 答案正确性        | 是 | expected_answer（**当前 200 题未标注 → N/A**）|
| faithfulness         | 忠实度            | 是 | 无（只需 context + answer）|
| answer_relevance     | 答案相关性        | 是 | 无（只需 question + answer）|

设计取舍（重要，别当成 bug）：
1. **chunk 主键 = (doc, chapter|article_no)**。`hybrid_retrieve` 返回的每个 source 就是一个
   注入 LLM 的父块，没有全局 chunk_id，所以用这个复合键当 chunk 身份。同一 doc 命中多个
   不同章节 = 多个 chunk，这正是 precision 要惩罚的对象。
2. **expected_docs 是文档级标注，不是 chunk 级**。因此 chunk_recall 的口径是
   「期望文档中有多少至少被一个 chunk 命中」，与旧版 doc 级 recall 数值一致但语义更清晰；
   chunk_precision 才是新增信息 —— 它衡量 K 个 chunk 里有多少块真正来自期望文档，
   直接反映"检索噪声"。想要真正的 chunk 级 recall 需要标到章节，属后续工作。
3. **LLM 裁判默认关闭**（`--judge` 开启）。200 题 × 3 个指标 = 600 次 LLM 调用，
   必然超时且烧额度；这三个指标是 P3 质量评分，用 `--limit` 抽样 20-30 题即可。
4. 裁判 prompt 写在本文件里，不进 prompt/prompts.yml —— 评测是研发工具，
   不属于业务提示词工程，混进去会污染线上场景清单。
"""
from __future__ import annotations

import json
import re

# ---------------- 检索侧：纯计算，不调 LLM ----------------


def chunk_key(src: dict) -> str:
    """chunk 身份 = 文档 + 章节/条号（见模块注释设计取舍 1）。"""
    loc = src.get("chapter") or src.get("article_no") or ""
    return f"{src.get('doc', '')}#{loc}"


def chunk_recall_at_k(expected_docs: list[str], sources: list[dict]) -> float:
    """chunk 级召回 @K：期望文档中被至少一个召回 chunk 覆盖的比例。"""
    if not expected_docs:
        return 0.0
    got_docs = {s.get("doc", "") for s in sources}
    return len(set(expected_docs) & got_docs) / len(set(expected_docs))


def chunk_precision_at_k(expected_docs: list[str], sources: list[dict]) -> float:
    """chunk 级精确率 @K：召回的 K 个 chunk 中，来自期望文档的比例（衡量噪声）。

    注意按 chunk 去重后再算 —— 同一父块被两条子块命中不应重复计数。
    """
    if not sources:
        return 0.0
    exp = set(expected_docs)
    uniq: dict[str, str] = {}
    for s in sources:
        uniq[chunk_key(s)] = s.get("doc", "")
    if not uniq:
        return 0.0
    return sum(1 for doc in uniq.values() if doc in exp) / len(uniq)


def context_recall(expected_docs: list[str], context: str, sources: list[dict]) -> float:
    """上下文召回：期望文档中有多少真正出现在拼接后的 context 里。

    与 chunk_recall 的区别：`hybrid_retrieve` 做父块去重时会丢弃重复父块，
    个别 source 可能对应不到 context 段落。这个指标测的是"最终喂给 LLM 的
    那段文本"覆盖了多少期望知识，是 LLM 能答对的上限。

    实现：优先解析 context 里的 `[来源: doc#章节]` 标注（rag.py 拼接时写入，最准）；
    context 为空或无标注时退回 sources 的 doc 集合。
    """
    if not expected_docs:
        return 0.0
    labeled = set(re.findall(r"\[来源: ([^#\]]+)", context))
    ctx_docs = labeled or {s.get("doc", "") for s in sources}
    return len(set(expected_docs) & ctx_docs) / len(set(expected_docs))

 
# ---------------- 生成侧：LLM 裁判 ----------------

_JUDGE_SYS = "你是严格的 RAG 评测裁判。只输出 JSON，不要解释、不要 markdown 代码块。"

_FAITHFULNESS_TMPL = """判断【答案】是否完全由【上下文】支撑（忠实度）。

评分规则：
- 把答案拆成若干事实陈述，统计其中能被上下文直接支撑的比例。
- 上下文没提到却在答案里出现的内容 = 幻觉，不计入支撑。
- 通用常识性套话（如"应加强管理"）不算幻觉，但也不算支撑，从分母剔除。
- **忽略归纳/过渡句**：如"主要原因如下""可归纳为""综上所述"等结构性引导语，不是事实陈述，不参与评分。
- **领域通用常识不算幻觉**：如"高处作业必须系安全带""无证上岗是违规"等安全生产行业共识，即使上下文未逐字出现，也视为合理背景知识，从分母剔除（不算支撑也不算幻觉）。
- **近义/包含匹配即可算支撑**：上下文用词与答案不同但意思一致（如上下文"临边作业应设置防护栏杆"支撑答案"临边洞口设防护栏杆、安全网、盖板"），不要因措辞差异判为幻觉。
- **具体实体/数字必须可溯源**：事故名称、日期、伤亡数字、罚款金额、条款号等精确信息，必须在上下文找到对应出处才算支撑；找不到出处且是具体事实 = 幻觉。

【上下文】
{context}

【答案】
{answer}

输出 JSON：{{"score": 0.0~1.0, "total_claims": 整数, "supported": 整数, "hallucinated": ["未被支撑的具体事实陈述（不含归纳句/常识）"]}}"""

_RELEVANCE_TMPL = """判断【答案】对【问题】的相关性（是否切题、有没有答非所问或大段无关铺陈）。

评分规则：
- 1.0 = 直接、完整地回应了问题；
- 0.5 = 部分相关，混入较多无关内容或只答了一半；
- 0.0 = 答非所问 / 拒答 / 空话。
- **拒答/空答不得判高分**：答案为空、或仅说"无法回答/资料不足"而没有给出任何问题相关内容的，一律 ≤ 0.3——即使问题本身在上下文中确实无答案，也要因未提供任何有用回应而低分。

【问题】
{question}

【答案】
{answer}

输出 JSON：{{"score": 0.0~1.0, "reason": "一句话理由"}}"""

_CORRECTNESS_TMPL = """对比【参考答案】判断【实际答案】的正确性。

评分规则：
- 关键事实、数字、条款一致 → 高分；
- 遗漏关键点按比例扣分；
- 与参考答案矛盾 → 重罚（0.2 以下）。

【问题】
{question}

【参考答案】
{reference}

【实际答案】
{answer}

输出 JSON：{{"score": 0.0~1.0, "missing": ["遗漏的关键点"], "contradictions": ["矛盾之处"]}}"""


def _judge(prompt: str) -> dict:
    """调 LLM 当裁判并解析 JSON。失败返回 {"score": None}，绝不伪造分数。"""
    from src.ai.llm_client import llm_client

    try:
        raw = llm_client.chat(
            [{"role": "system", "content": _JUDGE_SYS}, {"role": "user", "content": prompt}],
            temperature=0.0,
        )
    except Exception as e:  # 网络/额度问题不能让整轮评测崩
        return {"score": None, "error": f"LLM 调用失败: {e}"}
    return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    """容错解析：模型偶尔会包 ```json 代码块或前后加话。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {"score": None, "error": f"无法解析裁判输出: {text[:120]}"}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"score": None, "error": f"JSON 解析失败({e}): {text[:120]}"}
    s = data.get("score")
    if isinstance(s, (int, float)):
        data["score"] = max(0.0, min(1.0, float(s)))
    else:
        data["score"] = None
        data.setdefault("error", "裁判未返回数值 score")
    return data


def faithfulness(context: str, answer: str) -> dict:
    """忠实度：答案有多少比例被 context 支撑（幻觉检测）。"""
    if not answer.strip():
        return {"score": None, "error": "答案为空"}
    if not context.strip():
        return {"score": 0.0, "error": "context 为空，答案必属幻觉"}
    return _judge(_FAITHFULNESS_TMPL.format(context=context[:6000], answer=answer[:3000]))


def answer_relevance(question: str, answer: str) -> dict:
    """答案相关性：是否切题。"""
    if not answer.strip():
        return {"score": 0.0, "error": "答案为空"}
    return _judge(_RELEVANCE_TMPL.format(question=question, answer=answer[:3000]))


def answer_correctness(question: str, answer: str, reference: str | None) -> dict:
    """答案正确性：对比参考答案打分。

    **当前 golden_qa.jsonl 200 题没有 expected_answer 字段** —— 缺参考答案时
    返回 score=None 并标 N/A，不做任何猜测（用检索到的 context 自动生成"参考答案"
    会自证正确，指标失去意义）。后续人工标注 expected_answer 后本函数自动生效。
    """
    if not (reference or "").strip():
        return {"score": None, "na": True, "error": "缺 expected_answer 标注，跳过"}
    if not answer.strip():
        return {"score": 0.0, "error": "答案为空"}
    return _judge(
        _CORRECTNESS_TMPL.format(question=question, reference=reference[:3000], answer=answer[:3000])
    )
