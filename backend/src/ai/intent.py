"""意图识别（v2.0 大点 03）：回答前判「闲聊 / 知识问答」。

设计（裁定 1–4，见 `docs/v2.0/Verdict/03-意图识别.md`）：

1. **纯 LLM 单次二分类**：每轮一次调用，提示词 `chat_intent`，`temperature=0`、
   `max_tokens=CHAT_INTENT_MAX_TOKENS`、超时 `CHAT_INTENT_TIMEOUT`；**代码里不做关键词短路**。
2. **带上一轮**：输入包含上一轮问题与上一轮回答开头（由 `ChatService.last_round_hint()` 提供）。
   不带上下文时「第一问」「那…呢」这类指代句会被判成闲聊（调研实测踩过）。
3. **失败保守回退**：超时 / 异常 / 输出不可解析 → 一律 `knowledge`，并记 `degraded`；
   宁可多查一次，不可无依据作答。
4. `CHAT_INTENT_ENABLED=false` 时直接返回 `knowledge`（等于回到 01 收官行为，应急回滚）。

用法（`routers/chat.py`）：
    judged = intent.detect(question, hint=last_round_hint)
    if judged.is_chat:
        ...   # 跳过预处理与检索，用 chat_assistant 直答
"""
import json
import logging
import re
import time

from src.core.config import settings

logger = logging.getLogger(__name__)

LABELS = ("chat", "knowledge")
_INTENT_RE = re.compile(r'"(?:intent|label)"\s*:\s*"(chat|knowledge)"', re.IGNORECASE)
_REASON_RE = re.compile(r'"reason"\s*:\s*"([^"]{0,40})"')
_BARE_RE = re.compile(r"^\W*(chat|knowledge)\W*$", re.IGNORECASE)

# 判定专用客户端（懒加载）：调用级超时 + 不重试，避免 8s 被重试放大成 16s+
_client = None


def _model():
    global _client
    if _client is None:
        from langchain_openai import ChatOpenAI

        _client = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=0.0,
            timeout=settings.CHAT_INTENT_TIMEOUT,
            max_retries=0,
            max_tokens=settings.CHAT_INTENT_MAX_TOKENS,
        )
    return _client


class IntentResult:
    """判定结果（不用 dataclass，便于单测里直接构造 dict 风格的假对象）。"""

    __slots__ = ("label", "reason", "ms", "model", "degraded")

    def __init__(
        self,
        label: str = "knowledge",
        reason: str = "",
        ms: int = 0,
        model: str = "",
        degraded: dict | None = None,
    ) -> None:
        self.label = label if label in LABELS else "knowledge"
        self.reason = reason
        self.ms = ms
        self.model = model
        self.degraded = degraded

    @property
    def is_chat(self) -> bool:
        return self.label == "chat"

    def to_dict(self) -> dict:
        """落库 / SSE 用的视图（`refs.intent` 与 `meta.intent` 同一份）。"""
        out = {"label": self.label, "reason": self.reason, "ms": self.ms, "model": self.model}
        if self.degraded:
            out["degraded"] = self.degraded
        return out

    def __repr__(self) -> str:  # 便于日志/测试失败时排查
        return f"IntentResult(label={self.label!r}, reason={self.reason!r}, ms={self.ms})"


def _parse(content: str) -> tuple[str, str] | None:
    """宽容解析模型输出：JSON 行 → 裸词 → 花括号 JSON；都不行返回 None。"""
    text = (content or "").strip()
    if not text:
        return None
    m = _INTENT_RE.search(text)
    if m:
        reason = _REASON_RE.search(text)
        return m.group(1).lower(), (reason.group(1).strip() if reason else "")
    bare = _BARE_RE.match(text)
    if bare:
        return bare.group(1).lower(), ""
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict):
            label = str(obj.get("intent") or obj.get("label") or "").strip().lower()
            if label in LABELS:
                return label, str(obj.get("reason") or "")[:40]
    return None


def detect(question: str, hint: str = "") -> IntentResult:
    """判定本轮意图。永不抛异常：任何问题都退化成 knowledge。

    参数：
    - question：用户当前问题（原话）
    - hint：上一轮摘要（`ChatService.last_round_hint()` 的返回值），可空
    """
    q = (question or "").strip()
    if not q:
        return IntentResult(
            label="knowledge", reason="空问题", degraded={"kind": "intent", "why": "empty"}
        )
    if not settings.CHAT_INTENT_ENABLED:
        return IntentResult(label="knowledge", reason="意图门禁已关闭（CHAT_INTENT_ENABLED=false）")

    t0 = time.perf_counter()
    hint_text = (hint or "").strip()
    if len(hint_text) > settings.CHAT_INTENT_MAX_HISTORY_CHARS:
        # 截尾会切坏「上一轮问题」，改为按行保留靠前的（问题在前、回答在后）
        hint_text = hint_text[: settings.CHAT_INTENT_MAX_HISTORY_CHARS]
    try:
        from src.ai.prompts import render  # lazy：避免模块导入期就读 yml

        prompt = render("chat_intent", history=hint_text or "（无）", question=q[:500])
        resp = _model().invoke([{"role": "system", "content": prompt}])
        text = getattr(resp, "content", None) or str(resp)
        parsed = _parse(text)
        ms = int((time.perf_counter() - t0) * 1000)
        if parsed is None:
            logger.warning(
                "意图判定输出不可解析（%s），按知识问答继续: %s", q[:20], str(text)[:120]
            )
            return IntentResult(
                label="knowledge",
                reason="判定输出不可解析",
                ms=ms,
                model=settings.DEEPSEEK_MODEL,
                degraded={"kind": "intent", "why": "unparsable"},
            )
        label, reason = parsed
        return IntentResult(label=label, reason=reason, ms=ms, model=settings.DEEPSEEK_MODEL)
    except Exception as exc:  # noqa: BLE001 —— 判定失败不能挡住回答
        ms = int((time.perf_counter() - t0) * 1000)
        logger.warning("意图判定失败（%s），按知识问答继续: %s", q[:20], exc)
        return IntentResult(
            label="knowledge",
            reason=f"判定异常({type(exc).__name__})",
            ms=ms,
            model=settings.DEEPSEEK_MODEL,
            degraded={"kind": "intent", "why": type(exc).__name__},
        )
