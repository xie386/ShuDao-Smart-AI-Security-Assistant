"""本地重排模型封装：bge-reranker-v2-m3（CrossEncoder，LangChain 自定义组件）。

设计要点（AI_SOLUTION §5.3 + v2.0 大点 01）：
- 混合检索初召回 Top-30 → 本地重排精排 → 取 Top-4 喂大模型；
- LangChain 无原生 reranker，保留自研 CrossEncoder 实现，经 RunnableLambda 接入 LangChain 生态；
- lazy load：首次调用才加载；
- **精度可切**（v2.0 大点 01）：默认 fp16（实测 n=30 由 36.4s 降到 3.16s，top-4 排序与 fp32 完全一致），
  用户在 AI 助手页打开「高精度检索」时，该请求改用 fp32；
- 切换精度必须**先卸载旧模型再加载**：本机 RTX 3050 只有 4.29GB，fp32 时 bge-large(1.30GB)
  + reranker(2.27GB) 会把显存吃满（device_free 0.00GB），实际按 CPU 速度跑（56s/次）。

用法：
    from src.ai.reranker import reranker
    reranker.set_high_precision(True)     # 本请求要高精度（fp32）
    ranked = reranker.rerank("高处作业有哪些要求", [doc_a, doc_b, ...], top_k=4)
    # -> [(index, score), ...] 按分数降序
"""
import logging
import threading
from functools import lru_cache

from langchain_core.runnables import RunnableLambda

from src.core.config import settings

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self) -> None:
        self._model = None
        self._dtype: str | None = None      # 当前已加载模型的精度
        self._force_fp32 = False            # 请求级开关（high_precision=True）
        self._lock = threading.Lock()       # 精度切换互斥：避免并发请求来回卸载/重载

    # ---- 精度决策与加载 ----

    def _want_dtype(self) -> str:
        """本次该用哪种精度：请求级开关 > 配置；仅 cuda 下 fp16 才有意义。"""
        if settings.RERANKER_DEVICE != "cuda":
            return "fp32"
        want = "fp32" if self._force_fp32 else settings.RERANKER_DTYPE
        return want if want in ("fp16", "fp32") else "fp16"

    def _load(self) -> None:
        """（重新）加载重排模型；切换精度前必须卸载，否则 4GB 显存放不下两份。"""
        import torch
        from sentence_transformers import CrossEncoder  # lazy

        want = self._want_dtype()
        if self._model is not None:
            del self._model
            self._model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        model_kwargs: dict = {}
        if want == "fp16":
            model_kwargs["torch_dtype"] = torch.float16
        self._model = CrossEncoder(
            settings.RERANKER_MODEL_PATH,
            device=settings.RERANKER_DEVICE,
            model_kwargs=model_kwargs or None,
        )
        self._dtype = want
        logger.info(
            "重排模型已加载: %s (device=%s, dtype=%s, batch=%d)",
            settings.RERANKER_MODEL_PATH,
            settings.RERANKER_DEVICE,
            want,
            settings.RERANKER_BATCH_SIZE,
        )

    @property
    def model(self):
        """懒加载 CrossEncoder；精度与期望不符时重载（加锁）。"""
        if self._model is None or self._dtype != self._want_dtype():
            with self._lock:
                if self._model is None or self._dtype != self._want_dtype():
                    self._load()
        return self._model

    def set_high_precision(self, on: bool) -> None:
        """请求级精度开关：True → 本次用 fp32；False → 回到配置默认（fp16）。

        切换代价 = 重新加载一次模型（fp16≈4s / fp32≈24s），只在开关真的变化时才发生。
        """
        self._force_fp32 = bool(on)

    @property
    def current_dtype(self) -> str:
        """当前实际精度（未加载时按期望值报）。"""
        return self._dtype or self._want_dtype()

    # ---- 重排 ----

    def rerank(
        self, query: str, docs: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        """对 (query, doc) 对打分并返回 Top-k：[(原文索引, 分数)]，分数降序。"""
        if not docs:
            return []
        scores = self.model.predict(
            [[query, d] for d in docs],
            batch_size=settings.RERANKER_BATCH_SIZE,
            show_progress_bar=False,
        )
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
