"""考试工坊 · 出卷子智能体（复用主模型 llm_client + RAG 只读引用）。

业务（计划书 v2）：输入（提示词/文档文本）→ 按试卷模板生成试卷+参考答案（JSON）。
- 复用 src/ai/llm_client（LangChain ChatOpenAI，DeepSeek 主 / agnes 自动降级）；
- 知识依据：可选 hybrid_retrieve（Chroma safety_kb 混合检索，**只读引用**，不改任何 RAG 代码）；
- 试卷模板定义在 prompt/prompts.yml（exam_templates.*），主提示词 exam_paper_gen；
- 输出严格 JSON，经容错提取 + 逐题校验（防模型乱输出），失败自动重试 1 次。

用法：
    from src.ai.exam_agent import exam_agent
    paper = exam_agent.generate_paper("安全生产常识测试", "general", "高处作业要求", use_rag=False)
"""
import json
import logging
import re
from typing import Any

from src.ai.llm_client import llm_client
from src.ai.prompts import load_prompts, render

logger = logging.getLogger(__name__)

VALID_TYPES = {"single_choice", "multi_choice", "true_false", "essay"}


def _extract_json(text: str) -> Any:
    """容错提取 JSON：定位首个 { 到最后一个 }（兼容 markdown 代码块包裹）。"""
    start = text.find("{")
    if start < 0:
        start = text.find("[")
    end = text.rfind("}")
    if end < 0:
        end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("模型输出中未找到 JSON")
    return json.loads(text[start : end + 1])


def _validate_question(q: dict) -> str | None:
    """校验单题结构，返回错误原因（None=合法）。"""
    qtype = q.get("type")
    if qtype not in VALID_TYPES:
        return f"题型非法: {qtype}"
    if not q.get("content") or not str(q["content"]).strip():
        return "题干为空"
    if qtype in ("single_choice", "multi_choice"):
        options = q.get("options") or []
        if len(options) < 2:
            return "客观题选项少于2个"
        for letter in str(q.get("answer", "")).split(","):
            if letter.strip() not in "ABCDEFGH"[: len(options)]:
                return f"答案 {letter.strip()} 不在选项范围内"
    if qtype == "true_false" and str(q.get("answer", "")).strip() not in ("正确", "错误"):
        return "判断题答案必须为 正确/错误"
    if qtype == "essay" and not str(q.get("answer", "")).strip():
        return "简答题缺少参考答案"
    return None


def _validate_paper(data: dict) -> str | None:
    """校验整卷结构：questions 必须为非空数组，逐题合法。"""
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        return "试卷未包含题目（questions 为空）"
    for i, q in enumerate(questions):
        reason = _validate_question(q)
        if reason:
            return f"第 {i + 1} 题不合法: {reason}"
    return None


class ExamPaperAgent:
    """出卷子智能体：试卷模板 + 主模型生成 + JSON 容错与校验。"""

    def __init__(self) -> None:
        self._templates: dict | None = None

    # ---- 模板 ----

    def _template_defs(self) -> dict:
        if self._templates is None:
            self._templates = load_prompts().get("exam_templates", {})
        return self._templates

    def list_templates(self) -> list[dict]:
        """试卷模板列表（供前端选择/详情）。"""
        return [
            {
                "key": key,
                "name": val.get("name", key),
                "description": val.get("description", ""),
                "distribution": val.get("distribution", ""),
                "total_score": val.get("total_score", 100),
            }
            for key, val in self._template_defs().items()
        ]

    # ---- 生成 ----

    def generate_paper(
        self,
        title: str,
        template_key: str,
        input_text: str,
        use_rag: bool = True,
    ) -> dict:
        """按模板生成试卷 JSON。

        返回 {"title", "template", "total_score", "questions": [...]}；
        模板不存在抛 ValueError；生成/校验连续失败抛 RuntimeError（中文原因）。
        """
        defs = self._template_defs()
        if template_key not in defs:
            raise ValueError(f"未知试卷模板: {template_key}（可选: {list(defs)}）")

        # 1. 依据增强（可选）：只读引用 RAG；RAG 不可用/知识库为空时降级为纯 LLM 出题
        retrieved_context = ""
        if use_rag:
            try:
                from src.ai.rag import hybrid_retrieve  # lazy 引用，不改 RAG 代码

                retrieved_context, _sources = hybrid_retrieve(input_text, top_k=5)
                logger.info("考试出题 RAG 依据: %d 字符", len(retrieved_context))
            except Exception as exc:
                logger.warning("考试出题 RAG 依据获取失败，降级为纯 LLM: %s", exc)

        template = defs[template_key]
        template_definition = (
            f"题型分布：{template.get('distribution', '')}\n"
            f"要求：{template.get('rules', '')}"
        )
        system = render(
            "exam_paper_gen",
            exam_title=title,
            template_definition=template_definition,
            retrieved_context=retrieved_context or "（无知识库依据）",
            input_text=input_text,
        )

        # 2. 调用主模型（LangChain 自动降级 agnes）；校验不过/异常重试 1 次（换温度）
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "请开始出题。"},
        ]
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                raw = "".join(
                    llm_client.chat_stream(messages, temperature=0.4 if attempt == 0 else 0.7)
                )
                data = _extract_json(raw)
                reason = _validate_paper(data)
                if reason is None:
                    data.setdefault("title", title)
                    data.setdefault("total_score", template.get("total_score", 100))
                    return data
                last_err = RuntimeError(f"试卷校验未通过: {reason}")
                logger.warning("出卷第 %d 次未通过校验: %s", attempt + 1, reason)
            except Exception as exc:  # noqa: BLE001 —— 重试兜底
                last_err = exc
                logger.warning("出卷第 %d 次失败: %s", attempt + 1, exc)

        raise RuntimeError(f"AI 出卷失败（已重试一次）: {last_err}")


# 模块级单例：全项目复用
exam_agent = ExamPaperAgent()
