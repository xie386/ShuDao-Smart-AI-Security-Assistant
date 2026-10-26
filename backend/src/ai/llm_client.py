"""大模型客户端：DeepSeek 主 / agnes-2.5-flash 降级（LangChain 架构）。

文档决策（AI_SOLUTION §1）：主模型 deepseek-v4-flash，降级备选 agnes-2.5-flash，
视觉子模型 qwen3.8max（见 vision.py）。
基于 LangChain 1.x：ChatOpenAI（OpenAI 兼容端点）+ with_fallbacks 原生降级。

用法（接口签名与自研版一致，业务层零改动）：
    from src.ai.llm_client import llm_client
    answer = llm_client.chat([{"role": "user", "content": "高处作业有哪些要求？"}])
    for chunk in llm_client.chat_stream([...]):
        print(chunk, end="")
"""
import logging

from langchain_openai import ChatOpenAI

from src.core.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """LangChain ChatOpenAI 双模型客户端：主模型异常自动降级 agnes（with_fallbacks）。"""

    def __init__(self) -> None:
        self._primary: ChatOpenAI | None = None
        self._fallback: ChatOpenAI | None = None
        self._chain = None

    # ---- 客户端懒加载 ----

    def _primary_llm(self) -> ChatOpenAI:
        if self._primary is None:
            self._primary = ChatOpenAI(
                model=settings.DEEPSEEK_MODEL,
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                temperature=settings.LLM_TEMPERATURE,
                timeout=settings.LLM_TIMEOUT,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return self._primary

    def _fallback_llm(self) -> ChatOpenAI:
        if self._fallback is None:
            self._fallback = ChatOpenAI(
                model=settings.AGNES_MODEL,
                api_key=settings.AGNES_API_KEY,
                base_url=settings.AGNES_BASE_URL,
                temperature=settings.LLM_TEMPERATURE,
                timeout=settings.LLM_TIMEOUT,
                max_retries=settings.LLM_MAX_RETRIES,
            )
        return self._fallback

    @property
    def chain(self):
        """主模型 + with_fallbacks 降级链（invoke/stream 均生效）。"""
        if self._chain is None:
            self._chain = self._primary_llm().with_fallbacks([self._fallback_llm()])
            logger.info(
                "LangChain 链就绪: 主=%s 降级=%s",
                settings.DEEPSEEK_MODEL,
                settings.AGNES_MODEL,
            )
        return self._chain

    @property
    def primary_chat_model(self) -> ChatOpenAI:
        """裸主模型（v2.0 大点 01：`create_agent` 需要可 bind_tools 的 ChatModel，
        `with_fallbacks` 返回的是 Runnable 包装，不能直接当 model 传入）。
        Agent 路径的降级由调用方兜底：Agent 异常 → 回落 `chat_stream`（含 with_fallbacks）。
        """
        return self._primary_llm()

    # ---- 非流式 ----

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        """非流式对话：优先 DeepSeek，异常自动降级 agnes（LangChain fallbacks）。"""
        chain = self.chain.bind(temperature=temperature) if temperature is not None else self.chain
        resp = chain.invoke(messages)  # messages: [{"role","content"}, ...]
        content = resp.content if hasattr(resp, "content") else str(resp)
        if not content:
            raise RuntimeError("模型返回空内容")
        return content

    # ---- 流式（SSE，A03 用）----

    def chat_stream(self, messages: list[dict], temperature: float | None = None):
        """流式对话：yield 增量文本；主模型中途失败自动切换降级模型续答。"""
        chain = self.chain.bind(temperature=temperature) if temperature is not None else self.chain
        for chunk in chain.stream(messages):
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                yield text


# 模块级单例：全项目复用同一客户端（连接复用）
llm_client = LLMClient()
