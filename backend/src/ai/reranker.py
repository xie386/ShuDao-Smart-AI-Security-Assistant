"""本地重排模型封装：bge-reranker-base（CrossEncoder，LangChain 自定义组件）。

设计要点（AI_SOLUTION §5.3）：
- 混合检索初召回 Top-20 → 本地重排精排 → 取 Top-4 喂大模型；
- LangChain 无原生 reranker，保留自研 CrossEncoder 实现，经 RunnableLambda 接入 LangChain 生态；
- lazy load：首次调用才加载。

用法：
    from src.ai.reranker import reranker
    ranked = reranker.rerank("高处作业有哪些要求", [doc_a, doc_b, ...], top_k=4)
    # -> [(index, score), ...] 按分数降序
"""
import logging
from functools import lru_cache

from langchain_core.runnables import RunnableLambda

from src.core.config import settings

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self) -> None:
        self._model = None

    @property
    def model(self):
        """懒加载 CrossEncoder。"""
        if self._model is None:
            from sentence_transformers import CrossEncoder  # lazy

            self._model = CrossEncoder(
                settings.RERANKER_MODEL_PATH, device=settings.RERANKER_DEVICE
            )
            logger.info(
                "重排模型已加载: %s (device=%s)",
                settings.RERANKER_MODEL_PATH,
                settings.RERANKER_DEVICE,
            )
        return self._model

    def rerank(
        self, query: str, docs: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        """对 (query, doc) 对打分并返回 Top-k：[(原文索引, 分数)]，分数降序。"""
        if not docs:
            return []
        scores = self.model.predict([[query, d] for d in docs])
        k = min(top_k or settings.RAG_TOP_K_RERANK, len(docs))
        ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)[:k]
        return [(int(i), float(s)) for i, s in ranked]

    def as_runnable(self) -> RunnableLambda:
        """接入 LangChain 链：输入 {"query": str, "docs": [str]} → 重排结果。"""
        return RunnableLambda(
            lambda x: self.rerank(x["query"], x["docs"])
        )


@lru_cache
def get_reranker() -> Reranker:
    return Reranker()


reranker = get_reranker()
