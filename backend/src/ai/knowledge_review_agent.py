"""知识贡献 · 审核预筛子智能体（辅助管理员判定是否属于施工安全领域）。

依据：backend/docs/施工安全领域文件鉴别判定标准.md（浓缩进 prompts.yml 的
knowledge_review 模板；原文查看入口由前端提供，AI 辅助 + 硬性规章双保障）。

设计（对齐考试工坊 exam_agent 范式）：
- 复用 src/ai/llm_client（LangChain ChatOpenAI，DeepSeek 主 / agnes 自动降级）；
- prompt 模板：prompts.yml → knowledge_review（{title} {content} 占位符）；
- 输出严格 JSON（in_scope / suggestion / reason / matched），容错提取 + 校验；
- 失败自动重试 1 次（换温度）；
- 只做预筛，**不写库、不改 RAG 代码**，最终决策在管理员（K04）。

用法：
    from src.ai.knowledge_review_agent import review_agent
    result = review_agent.review("煤矿安全规程", "第一章 总则...", "laws")
    # -> {"in_scope": false, "suggestion": "驳回", "reason": "...", "matched": [...]}
"""
import json
import logging

from src.ai.llm_client import llm_client
from src.ai.prompts import render

logger = logging.getLogger(__name__)

_CONTENT_MAX = 3000  # 正文截断长度（防超 token；标题全量）


def _extract_json(text: str) -> dict:
    """容错提取 JSON：定位首个 { 到最后一个 }（兼容 markdown 代码块包裹）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise ValueError("模型输出中未找到 JSON")
    return json.loads(text[start : end + 1])


def _validate(result: dict) -> str | None:
    """校验预筛结果结构，返回错误原因（None=合法）。"""
    if "suggestion" not in result or result["suggestion"] not in ("通过", "驳回", "复核"):
        return f"suggestion 非法: {result.get('suggestion')}"
    if not result.get("reason"):
        return "reason 为空"
    return None


class KnowledgeReviewAgent:
    """审核预筛子智能体：判定上传内容是否属于施工安全领域。"""

    def review(self, title: str, content: str, doc_type: str = "") -> dict:
        """对单条贡献做预筛。

        返回 {"in_scope", "suggestion", "reason", "matched", "doc_type"}；
        连续失败抛 RuntimeError（中文原因，service 层转 HTTPException）。
        """
        system = render(
            "knowledge_review",
            title=(title or "").strip()[:200],
            content=(content or "").strip()[:_CONTENT_MAX],
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "请按判定标准输出预筛结论。"},
        ]
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                raw = "".join(
                    llm_client.chat_stream(messages, temperature=0.2 if attempt == 0 else 0.5)
                )
                result = _extract_json(raw)
                reason = _validate(result)
                if reason is None:
                    result["doc_type"] = doc_type
                    result.setdefault("matched", [])
                    return result
                last_err = RuntimeError(f"预筛结果校验未通过: {reason}")
                logger.warning("审核预筛第 %d 次未通过校验: %s", attempt + 1, reason)
            except Exception as exc:  # noqa: BLE001 —— 重试兜底
                last_err = exc
                logger.warning("审核预筛第 %d 次失败: %s", attempt + 1, exc)
        raise RuntimeError(f"审核预筛失败（已重试一次）: {last_err}")


# 模块级单例：全项目复用
review_agent = KnowledgeReviewAgent()
