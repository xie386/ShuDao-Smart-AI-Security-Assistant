"""本地嵌入模型封装：bge-large-zh（sentence-transformers）。

设计要点（AI_SOLUTION §4.4）：
- 本地模型 D:/LLM/model/bge-large-zh，CPU 推理，免 API、离线可用；
- **lazy load**：首次调用才加载模型（约 1.3GB），不拖慢核心后端启动；
- 查询与文档统一经本模块嵌入，入库/检索均显式传向量，不依赖 Chroma 默认 MiniLM；
- 查询前加 bge 官方指令前缀（settings.EMBEDDING_QUERY_PREFIX），提升检索相关性。

用法：
    from src.ai.embeddings import embedding_model
    vec = embedding_model.embed_query("高处作业有哪些要求")
    vecs = embedding_model.embed_documents([...])
"""
import logging
from functools import lru_cache

from src.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingModel:
    def __init__(self) -> None:
        self._model = None

    @property
    def model(self):
        """懒加载 SentenceTransformer（首次调用约 30-60s）。"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy

            self._model = SentenceTransformer(
                settings.EMBEDDING_MODEL_PATH, device=settings.EMBEDDING_DEVICE
            )
            logger.info(
                "嵌入模型已加载: %s (device=%s, dim=%s)",
                settings.EMBEDDING_MODEL_PATH,
                settings.EMBEDDING_DEVICE,
                self._model.get_embedding_dimension(),
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文档/分块编码，归一化后返回（与 Chroma 余弦距离匹配）。"""
        if not texts:
            return []
        vectors = self.model.encode(
            texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        """单条查询编码：自动加 bge 指令前缀。"""
        prefixed = f"{settings.EMBEDDING_QUERY_PREFIX}{text}"
        return self.model.encode(prefixed, normalize_embeddings=True).tolist()


@lru_cache
def get_embedding_model() -> EmbeddingModel:
    return EmbeddingModel()


embedding_model = get_embedding_model()
