"""本地重排模型封装：bge-reranker-base（CrossEncoder）。

设计要点（AI_SOLUTION §5.3）：
- 混合检索初召回 Top-20 → 本地重排精排 → 取 Top-4 喂大模型；
- lazy load：首次调用才加载；
- 比向量相似度更懂"问题与片段是否真相关"。

用法：
    from src.ai.reranker import reranker
    ranked = reranker.rerank("高处作业有哪些要求", [doc_a, doc_b, ...], top_k=4)
    # -> [(index, score), ...] 按分数降序
"""
import logging
"""
注释文档
logging 日志
logger.info(信息,模型路径,设备)
 日志格式：%(asctime)s - %(name)s)s - %(levelname)s - %(message)s
 作用：记录重排模型加载信息，包括模型路径和设备。以方便调试。
"""
from functools import lru_cache
"""
注释文档
lru_cache 缓存
作用：缓存重排模型实例，避免重复加载。
"""

from src.core.config import settings
"""
注释文档
settings 模块
作用：导入配置参数，包括模型路径和设备。
"""

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


@lru_cache
def get_reranker() -> Reranker:
    return Reranker()


reranker = get_reranker()
