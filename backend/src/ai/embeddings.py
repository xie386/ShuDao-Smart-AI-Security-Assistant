"""本地嵌入模型封装：bge-large-zh（LangChain HuggingFaceEmbeddings）。

设计要点（AI_SOLUTION §4.4）：
- 本地模型 D:/LLM/model/bge-large-zh，默认 GPU/CUDA（EMBEDDING_DEVICE 可切 cpu）；
- **lazy load**：首次调用才加载模型（约 1.3GB），不拖慢核心后端启动；
- 查询前加 bge 官方指令前缀，提升检索相关性；
- 接口签名与自研版一致（embed_query/embed_documents），业务层零改动。

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
        """懒加载 HuggingFaceEmbeddings（底层 sentence-transformers，首次约 30-60s）。"""
        if self._model is None:
            from langchain_huggingface import HuggingFaceEmbeddings  # lazy

            self._model = HuggingFaceEmbeddings(
                model_name=settings.EMBEDDING_MODEL_PATH,
                model_kwargs={"device": settings.EMBEDDING_DEVICE},
                encode_kwargs={"normalize_embeddings": True},
            )
            logger.info(
                "嵌入模型已加载(LangChain): %s (device=%s)",
                settings.EMBEDDING_MODEL_PATH,
                settings.EMBEDDING_DEVICE,
            )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量文档/分块编码，归一化后返回（与 Chroma 余弦距离匹配）。"""
        if not texts:
            return []
        return self.model.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """单条查询编码：自动加 bge 指令前缀。"""
        prefixed = f"{settings.EMBEDDING_QUERY_PREFIX}{text}"
        return self.model.embed_query(prefixed)


@lru_cache
def get_embedding_model() -> EmbeddingModel:
    return EmbeddingModel()


embedding_model = get_embedding_model()
