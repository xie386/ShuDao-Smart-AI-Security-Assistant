"""知识库构建与 Chroma 集合管理（safety_kb）。

覆盖 AI_SOLUTION §4 设计：
- Chroma 持久化集合（cosine 距离）初始化；
- 结构感知分块：法规按条 / 操作规程按步骤 / 应急预案按阶段 / 事故案例按字段；
- 父子块：子块用于精确检索，父块（完整章节/条目）元数据保留，检索命中后返回父块喂 LLM；
- 批量入库 upsert（显式传本地 bge-large-zh 向量）。

lazy import chromadb，保证核心后端启动不受影响。
"""
import logging
import re
import uuid
from pathlib import Path

from src.core.config import settings
from src.ai.embeddings import embedding_model

logger = logging.getLogger(__name__)

_collection = None

# ---- Chroma 集合 ----

_DOC_TYPE_MAP = {
    "laws": "法规/制度",
    "procedures": "操作规程",
    "plans": "应急预案",
    "cases": "事故案例",
}


def get_collection():
    """获取 safety_kb 集合（lazy 初始化 PersistentClient）。"""
    global _collection
    if _collection is None:
        import chromadb  # lazy

        client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
        _collection = client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Chroma 集合就绪: %s (dir=%s)", settings.CHROMA_COLLECTION, settings.CHROMA_DIR)
    return _collection


# ---- 结构感知分块（AI_SOLUTION §4.2）----

def split_law(text: str, doc: str, source: str = "") -> list[dict]:
    """法规/制度：以"第X条"为单元切分，章作为父级元数据。"""
    chunks = []
    chapter = ""
    article_pattern = re.compile(r"(?=第[一二三四五六七八九十百千0-9]+条)")
    for part in article_pattern.split(text):
        part = part.strip()
        if not part:
            continue
        chapter_match = re.search(r"第[一二三四五六七八九十]+章[^\n]*", part)
        if chapter_match:
            chapter = chapter_match.group(0).strip()
            # 章节标题与条款合并为一块
        article_match = re.search(r"^第[一二三四五六七八九十百千0-9]+条", part)
        chunks.append(
            {
                "text": part,
                "metadata": {
                    "doc": doc,
                    "doc_type": _DOC_TYPE_MAP.get("laws", "法规/制度"),
                    "chapter": chapter,
                    "article_no": article_match.group(0) if article_match else "",
                    "tags": [],
                    "source": source,
                },
            }
        )
    return chunks


def split_procedure(text: str, doc: str, source: str = "") -> list[dict]:
    """操作规程：按编号步骤（1. 2. / 第一步 / 作业前/中/后）切分。"""
    chunks = []
    step_pattern = re.compile(r"(?=(?:^\d+[\.、]|第[一二三四五六七八九十]+步|作业前|作业中|作业后))", re.M)
    for part in step_pattern.split(text):
        part = part.strip()
        if not part:
            continue
        chunks.append(
            {
                "text": part,
                "metadata": {
                    "doc": doc,
                    "doc_type": _DOC_TYPE_MAP["procedures"],
                    "chapter": "",
                    "article_no": "",
                    "tags": [],
                    "source": source,
                },
            }
        )
    return chunks


def split_plan(text: str, doc: str, source: str = "") -> list[dict]:
    """应急预案：按响应阶段（预警/响应/处置/恢复）切分。"""
    chunks = []
    phase_pattern = re.compile(r"(?=(?:预警|应急响应|处置|恢复|保障)[^\n]{0,10}[\n])")
    parts = phase_pattern.split(text)
    if len(parts) <= 1:  # 未命中阶段词则整段一块
        parts = [text]
    for part in parts:
        part = part.strip()
        if not part:
            continue
        chunks.append(
            {
                "text": part,
                "metadata": {
                    "doc": doc,
                    "doc_type": _DOC_TYPE_MAP["plans"],
                    "chapter": "",
                    "article_no": "",
                    "tags": [],
                    "source": source,
                },
            }
        )
    return chunks


def split_case(text: str, doc: str, source: str = "") -> list[dict]:
    """事故案例：按结构化字段（经过/原因/教训）切分并打事故类型标签。"""
    chunks = []
    field_pattern = re.compile(r"(?=(?:事故经过|事故原因|教训|防范措施)[：:])")
    for part in field_pattern.split(text):
        part = part.strip()
        if not part:
            continue
        chunks.append(
            {
                "text": part,
                "metadata": {
                    "doc": doc,
                    "doc_type": _DOC_TYPE_MAP["cases"],
                    "chapter": "",
                    "article_no": "",
                    "tags": [],  # TODO(D3): 从文件名/内容提取 accident_type
                    "source": source,
                },
            }
        )
    return chunks


SPLITTERS = {
    "laws": split_law,
    "procedures": split_procedure,
    "plans": split_plan,
    "cases": split_case,
}


# ---- 入库 ----

def upsert_chunks(chunks: list[dict]) -> int:
    """分块嵌入后批量写入 safety_kb。返回写入条数。"""
    if not chunks:
        return 0
    texts = [c["text"] for c in chunks]
    vectors = embedding_model.embed_documents(texts)
    ids = [str(uuid.uuid4()) for _ in chunks]
    metadatas = [c["metadata"] for c in chunks]
    get_collection().upsert(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)
    logger.info("已写入 %d 块到 %s", len(chunks), settings.CHROMA_COLLECTION)
    return len(chunks)


def build_from_dir(src_dir: str | Path | None = None) -> dict:
    """扫描知识库源目录（四类子目录），分块→嵌入→入库。

    目录约定（settings.KNOWLEDGE_SRC_DIR）:
        knowledge_src/
        ├── laws/        法规/制度（.md/.txt）
        ├── procedures/  操作规程
        ├── plans/       应急预案
        └── cases/       事故案例
    返回 {"total_chunks": n, "by_type": {...}}
    """
    src = Path(src_dir or settings.KNOWLEDGE_SRC_DIR)
    if not src.exists():
        raise FileNotFoundError(f"知识库源目录不存在: {src}（请先创建四类子目录并放入文档）")

    total = 0
    by_type: dict[str, int] = {}
    for sub, splitter in SPLITTERS.items():
        sub_dir = src / sub
        if not sub_dir.is_dir():
            continue
        for f in sorted(sub_dir.glob("*.md")) + sorted(sub_dir.glob("*.txt")):
            text = f.read_text(encoding="utf-8")
            chunks = splitter(text, doc=f.stem, source=str(f))
            n = upsert_chunks(chunks)
            total += n
            by_type[sub] = by_type.get(sub, 0) + n
            logger.info("  %s: %d 块", f.name, n)
    return {"total_chunks": total, "by_type": by_type}
