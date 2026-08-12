"""大模型客户端：DeepSeek 主 / agnes-2.5-flash 降级，统一 OpenAI 兼容 SDK。

文档决策（AI_SOLUTION §1）：主模型 deepseek-v4-flash，降级备选 agnes-2.5-flash，
视觉子模型 qwen3.8max（图片隐患排查，后续接入）。
所有端点经 `openai` 包统一接入（改 base_url/api_key），便于热切换。

用法：
    from src.ai.llm_client import llm_client
    answer = llm_client.chat([{"role": "user", "content": "高处作业有哪些要求？"}])
    for chunk in llm_client.chat_stream([...]):
        print(chunk, end="")
"""
import logging
from typing import Iterable

from openai import OpenAI

from src.core.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """OpenAI 兼容多模型客户端：主模型调用异常自动降级 agnes。"""

    def __init__(self) -> None:
        self._primary: OpenAI | None = None
        self._fallback: OpenAI | None = None

    # ---- 客户端懒加载 ----

    def _primary_client(self) -> OpenAI:
        if self._primary is None:
            self._primary = OpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return self._primary

    def _fallback_client(self) -> OpenAI:
        if self._fallback is None:
            self._fallback = OpenAI(
                api_key=settings.AGNES_API_KEY,
                base_url=settings.AGNES_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return self._fallback

    # ---- 非流式 ----

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        """非流式对话：优先 DeepSeek，异常自动降级 agnes，再失败抛出。"""
        try:
            return self._complete(
                self._primary_client(), settings.DEEPSEEK_MODEL, messages, temperature
            )
        except Exception as exc:
            logger.warning("DeepSeek 调用失败(%s)，降级 agnes", exc)
            return self._complete(
                self._fallback_client(), settings.AGNES_MODEL, messages, temperature
            )

    # ---- 流式（SSE，A03 用）----

    def chat_stream(self, messages: list[dict], temperature: float | None = None) -> Iterable[str]:
        """流式对话：yield 增量文本；主模型中途失败自动切换降级模型续答。"""
        try:
            yield from self._stream(
                self._primary_client(), settings.DEEPSEEK_MODEL, messages, temperature
            )
        except Exception as exc:
            logger.warning("DeepSeek 流式失败(%s)，降级 agnes", exc)
            yield from self._stream(
                self._fallback_client(), settings.AGNES_MODEL, messages, temperature
            )

    # ---- 内部实现 ----

    @staticmethod
    def _complete(client: OpenAI, model: str, messages: list[dict], temperature: float | None) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError(f"{model} 返回空内容")
        return content

    @staticmethod
    def _stream(
        client: OpenAI, model: str, messages: list[dict], temperature: float | None
    ) -> Iterable[str]:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
            stream=True,
        )
        for chunk in resp:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content


# 模块级单例：全项目复用同一客户端（连接复用）
llm_client = LLMClient()
