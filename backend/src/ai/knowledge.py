"""知识库构建与 Chroma 集合管理（safety_kb）。

严格按 AI_SOLUTION §4 分块策略实现：
- §4.1 分块总原则：语义单元完整优先，目标单块 300-800 字；超长语义单元按句滑窗
  二次切分、相邻块重叠 15% 防边界信息丢失；每块必带元数据
  {doc, doc_type, chapter, article_no, tags, source}；
- §4.2 结构感知分块：法规按"条"/规程按"步骤"/预案按"阶段"/案例按"字段"；
- §4.3 父子分块（Small-to-Big）：**子块**细粒度用于精确向量检索；**父块**保留所属
  章节/整篇完整文本，检索命中子块后由 metadata.parent_text 返回父块喂 LLM；
- §4.5 入库与更新：稳定 ID（内容哈希）实现增量 upsert，重跑不重复。

lazy import chromadb，保证核心后端启动不受影响。
"""
import hashlib
import logging
import re
import uuid
from pathlib import Path

from src.core.config import settings
from src.ai.embeddings import embedding_model

logger = logging.getLogger(__name__)

_collection = None

# ---- 分块参数（AI_SOLUTION §4.1）----
CHUNK_MIN, CHUNK_MAX = 300, 800
OVERLAP_RATIO = 0.15  # 相邻块重叠比例，防边界信息丢失

# ---- Chroma 集合 ----

_DOC_TYPE_MAP = {
    "laws": "法规/制度",
    "procedures": "操作规程",
    "plans": "应急预案",
    "cases": "事故案例",
}


def get_collection():
    """获取 safety_kb 集合（lazy 初始化 PersistentClient）。

    写入侧 chromadb 直连（显式传本地向量 + 自研结构分块）；
    检索侧走 LangChain（get_vectorstore().as_retriever()）。
    """
    global _collection
    if _collection is None:
        import chromadb  # lazy
        # chromadb 本地持久化客户端：数据保存在磁盘目录，进程重启不丢失
        client = chromadb.PersistentClient(path=settings.CHROMA_DIR)
        # 获取或创建集合：HNSW 索引 + cosine 距离，用于向量检索
        _collection = client.get_or_create_collection(
            name=settings.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Chroma 集合就绪: %s (dir=%s)", settings.CHROMA_COLLECTION, settings.CHROMA_DIR)
    return _collection


def get_vectorstore():
    """LangChain 检索端：langchain_chroma.Chroma（与 get_collection 同一持久化目录）。

    D3 混合检索用法：
        from src.ai.knowledge import get_vectorstore
        retriever = get_vectorstore().as_retriever(search_kwargs={"k": 20})
        docs = retriever.invoke(question)  # -> list[Document]（含 metadata.parent_text）
    """
    from langchain_chroma import Chroma  # lazy
    # LangChain 向量存储封装：统一接口 .as_retriever()，内部仍操作同一 Chroma 集合
    return Chroma(
        collection_name=settings.CHROMA_COLLECTION,
        embedding_function=embedding_model.model,  # 本地 bge-large-zh 嵌入模型
        persist_directory=settings.CHROMA_DIR,      # 与 get_collection() 同一持久化目录
        collection_metadata={"hnsw:space": "cosine"}, # HNSW 索引 + cosine 距离，用于向量检索
    )


# ---- §4.1 长度治理：滑窗切分 + 重叠 ----

def _overlap_split(text: str) -> list[str]:
    """超长语义单元按句滑窗二次切分：单块 ≤CHUNK_MAX，相邻块重叠 15%。

    仅用于兜底（如超长"条"）；结构单元本身不重叠（语义独立，上下文由父块保证）。
    """
    sentences = [s.strip() for s in re.split(r"(?<=[。；;！？!？\n])", text) if s.strip()]
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) <= CHUNK_MAX:
            cur += s
            continue
        if cur:
            chunks.append(cur)
        # 重叠：取上一块尾部 15% 字符续接到新块开头（防边界信息丢失）
        overlap = cur[-int(CHUNK_MAX * OVERLAP_RATIO):] if cur else ""
        cur = overlap + s
        if len(cur) > CHUNK_MAX:  # 单句仍超长：硬切
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def _pack_units(units: list[str], parent_text: str, **meta) -> list[dict]:
    """把语义单元包装为入库记录（child 超长时二次切分，统一挂 parent_text）。"""
    out: list[dict] = []
    for u in units:
        u = u.strip()
        if not u:
            continue
        pieces = _overlap_split(u) if len(u) > CHUNK_MAX else [u]
        for p in pieces:
            out.append({"text": p, "parent_text": parent_text, "metadata": dict(meta)})
    return out


def _pack_parts(parts: list[str], **meta) -> list[dict]:
    """每个 part 独立为父块（parent_text = part 自身），而非整篇共享一个父块。

    v5.0 核心修复：父块粒度从"整篇"改为"切分单元"（预案=阶段 / 案例=字段 /
    规程=步骤），解决精确率被 parent 去重结构性压死的问题——

    根因（v1.0~v4.0 精确率 0.25→0.13）：plans/cases/procedures 的父块 = 整篇，
    一篇文档的所有命中子块共享同一 parent_text，检索时父块注入去重后合并成
    1 个父块席，期望文档只能占 1/10，精确率上限被压死在 0.10。
    改为按切分单元做父块后，一篇文档贡献多个父块席（阶段/字段/步骤各占一席），
    精确率立即回升，同时父块变短更适合喂 LLM（整篇 1.6~5 万字符本就超窗口）。

    子块 text 不变 → 稳定 ID 不变 → 增量 upsert 自动覆盖 parent_text，无需 delete。
    """
    out: list[dict] = []
    for part in parts:
        out.extend(_pack_units([part], part, **meta))
    return out


def _extract_heading(part: str, limit: int = 30) -> str:
    """提取 part 首行作为章节标识（v6.0，供 chunk_key 区分同一文档的切分单元）。

    v5.0 改了父块粒度（parent_text=切分单元），但 metadata.chapter/article_no 仍为空，
    导致评测的 chunk_key（doc + chapter/article_no）退化成 doc，一个文档无论父块分多细
    都算 1 个 chunk，精确率被压死在 1/10（v5.0 精确率 0.13→0.15 没救回的原因）。

    v6.0：给切分单元写 chapter 标识（预案=阶段名 / 案例=字段名 / 规程=步骤号），
    让 chunk_key = doc#切分单元，一个文档贡献多个 chunk，精确率才能体现父块粒度。
    """
    for line in part.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:limit]
    return ""


def _pack_parts(parts: list[str], **meta) -> list[dict]:
    """每个 part 独立为父块（parent_text = part），并写章节标识（v5.0+v6.0）。"""
    out: list[dict] = []
    for part in parts:
        m = dict(meta)
        m["chapter"] = _extract_heading(part)  # v6.0：chunk_key 用切分单元标题
        out.extend(_pack_units([part], part, **m))
    return out


# ---- §4.2 结构感知分块（四类）----

def split_law(text: str, doc: str, source: str = "") -> list[dict]:
    """法规/制度：按"第X章→第X条"两级切分。

    子块 = 单条；父块 = 所属章完整文本（含章标题与全部条），无章则父块=整篇。
    """
    out: list[dict] = []
    chapter_parts = re.split(r"(?=第[一二三四五六七八九十]+章)", text)
    if len(chapter_parts) <= 1:  # 无章结构：整篇为父块
        out.extend(_process_law_articles(text, doc, source, parent_text=text, chapter=""))
    else:
        for cp in chapter_parts:
            cp = cp.strip()
            if not cp:
                continue
            m = re.search(r"第[一二三四五六七八九十]+章[^\n]*", cp)
            chapter = m.group(0).strip() if m else "（前言）"
            out.extend(_process_law_articles(cp, doc, source, parent_text=cp, chapter=chapter))
    return out


def _process_law_articles(text: str, doc: str, source: str, parent_text: str, chapter: str) -> list[dict]:
    parts = [p.strip() for p in re.split(r"(?=第[一二三四五六七八九十百千0-9]+条)", text) if p.strip()]
    records = _pack_units(
        parts, parent_text,
        doc=doc, doc_type=_DOC_TYPE_MAP["laws"], chapter=chapter,
        article_no="", tags=[], source=source,
    )
    for r in records:
        m = re.match(r"^第[一二三四五六七八九十百千0-9]+条", r["text"])
        r["metadata"]["article_no"] = m.group(0) if m else ""
    return records


def split_procedure(text: str, doc: str, source: str = "") -> list[dict]:
    """操作规程：按编号步骤/作业阶段切分，父块=步骤/阶段（v5.0）。"""
    parts = [p.strip() for p in re.split(
        r"(?=(?:^\d+[\.、]|第[一二三四五六七八九十]+步|作业前|作业中|作业后))", text, flags=re.M
    ) if p.strip()]
    return _pack_parts(
        parts,
        doc=doc, doc_type=_DOC_TYPE_MAP["procedures"], chapter="",
        article_no="", tags=[], source=source,
    )


# ---- v4.0 主题标签（治 plans 类 168 篇同质化，精确率 0.13 的根因）----
# 应急预案主题极杂（校园/消防/食品/危化品/地震…），主题藏在标题或文件名里。
# 入库时提取主题写入 metadata.tags，检索侧按主题加权，让同主题文档不再互相挤占。

# 内容标题关键词 → 主题标签（shd-plan 主力来源，主题在 "# 标题" 第一行）
_PLAN_TOPIC_TITLE: list[tuple[list[str], str]] = [
    (["消防", "火灾", "灭火", "防火"], "消防"),
    (["危化品", "危险化学品", "易制毒"], "危化品"),
    (["食品", "食物中毒", "饮食"], "食品"),
    (["地震", "防震"], "地震"),
    (["溺水", "踩踏", "欺凌", "校园", "学校", "中学", "小学", "职业"], "校园"),
    (["高处坠落", "坠落", "坠物"], "高处坠落"),
    (["机械伤害", "机械"], "机械"),
    (["矿山", "坍塌"], "矿山"),
    (["环境风险"], "环保"),
    (["洪水", "洪涝", "干旱", "极端天气", "台风", "暴雨"], "自然灾害"),
]

# 文件名关键词 → 主题标签（gov/local/mot 类，主题明确在英文文件名）
_PLAN_TOPIC_FILENAME: list[tuple[str, str]] = [
    ("earthquake", "地震"),
    ("flood-drought", "洪涝"),
    ("forest-grassland-fire", "森林火灾"),
    ("marine-oil-spill", "海洋溢油"),
    ("natural-disaster-relief", "自然灾害"),
    ("urban-rail", "城市轨道"),
    ("transport", "交通"),
    ("highway", "交通"),
    ("road-waterway", "交通"),
    ("traffic", "交通"),
]


def _extract_plan_topic(text: str, doc: str) -> list[str]:
    """从预案标题/文件名提取主题标签（v4.0 改动 1）。

    优先取内容标题（`# 标题` 首行）关键词，命中则返回（可能多主题，
    如"小学防震防火演练"→ [校园, 地震, 消防]）；标题命中不到再退回文件名
    关键词；都命中不到返回空列表（保持 v3.0 行为，不硬塞）。
    """
    title = ""
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            title = line
            break
    topics: list[str] = []
    for kws, topic in _PLAN_TOPIC_TITLE:
        if any(kw in title for kw in kws):
            topics.append(topic)
    if topics:
        return topics
    doc_lower = doc.lower()
    for kw, topic in _PLAN_TOPIC_FILENAME:
        if kw in doc_lower:
            return [topic]
    return []


def split_plan(text: str, doc: str, source: str = "") -> list[dict]:
    """应急预案：按响应阶段（预警/响应/处置/恢复/保障）切分，父块=阶段（v5.0）。

    v4.0：写入主题标签（_extract_plan_topic），供检索侧主题加权。
    """
    parts = [p.strip() for p in re.split(
        r"(?=(?:预警|应急响应|处置|恢复|保障)[^\n]{0,10}[\n])", text
    ) if p.strip()]
    if len(parts) <= 1:  # 未命中阶段词：整篇一块
        parts = [text]
    return _pack_parts(
        parts,
        doc=doc, doc_type=_DOC_TYPE_MAP["plans"], chapter="",
        article_no="", tags=_extract_plan_topic(text, doc), source=source,
    )


def split_case(text: str, doc: str, source: str = "") -> list[dict]:
    """事故案例：按结构化字段（经过/原因/教训/防范措施）切分，父块=字段（v5.0）。

    v5.0 修复：旧正则要求"字段名+冒号"（如"事故原因："），但真实文档是
    "编号+字段名"（如"四、事故原因"、"（一）事故经过。"、"## 二、事故经过及…"），
    切分从未生效，所有案例都是"整篇一块"（父块=整篇，精确率被 parent 去重压死）。
    改为行首宽松匹配真实字段标题格式。
    """
    parts = [p.strip() for p in re.split(
        r"(?m)^(?=[^\n]{0,20}(?:事故经过|事故原因|主要教训|防范措施))", text
    ) if p.strip()]
    if len(parts) <= 1:  # 未命中字段词：整篇一块
        parts = [text]
    return _pack_parts(
        parts,
        doc=doc, doc_type=_DOC_TYPE_MAP["cases"], chapter="",
        article_no="", tags=[], source=source,
    )


SPLITTERS = {
    "laws": split_law,
    "procedures": split_procedure,
    "plans": split_plan,
    "cases": split_case,
}


# ---- §4.5 入库（稳定 ID 增量 upsert）----

def _stable_id(chunk: dict) -> str:
    """内容哈希 ID：doc_type|doc|chapter|article_no|child文本前64字 → sha1 前16位。

    同一文本重跑得到同一 ID → upsert 覆盖，天然去重（增量入库）。
    """
    meta = chunk["metadata"]
    key = "|".join([
        meta.get("doc_type", ""), meta.get("doc", ""), meta.get("chapter", ""),
        meta.get("article_no", ""), chunk["text"][:64],
    ])
    # hashlib.sha1：对"类型|文档|章节|条款|文本前缀"做哈希，保证同一内容重跑得到相同 ID
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def upsert_chunks(chunks: list[dict]) -> int:
    """子块入库：向量=子块文本；documents=子块文本；metadata 携带 parent_text。

    检索命中子块后，从 metadata["parent_text"] 取父块喂 LLM（§4.3）。
    """
    if not chunks:
        return 0
    texts = [c["text"] for c in chunks]
    vectors = embedding_model.embed_documents(texts)
    # 稳定 ID + 文件内重复文本序号后缀（源文档"目录vs正文"可能重复，如预案 md；
    # 重跑顺序稳定 → 序号稳定 → 仍可增量覆盖去重）
    seen: dict[str, int] = {}
    ids = []
    for c in chunks:
        base = _stable_id(c)
        n = seen.get(base, 0)
        seen[base] = n + 1
        ids.append(base if n == 0 else f"{base}_{n}")
    metadatas = []
    for c in chunks:
        m = dict(c["metadata"])
        if not m.get("tags"):
            # Chroma 不允许空 list 元数据值（实测 ValueError）；空 tags 直接不写字段
            m.pop("tags", None)
        m["parent_text"] = c["parent_text"]
        metadatas.append(m)
    # Chroma 单次 upsert 有硬上限（max_batch_size，实测 5461，源于 SQLite 变量数限制）；
    # 大规模入库（D6：2.5 万块）必须分批写，否则报
    # "Batch size of N is greater than max batch size of 5461"。
    coll = get_collection()
    batch = min(getattr(coll._client, "get_max_batch_size", lambda: 5000)(), 5000) or 5000
    total = len(chunks)
    for i in range(0, total, batch):
        j = min(i + batch, total)
        coll.upsert(
            ids=ids[i:j], documents=texts[i:j],
            embeddings=vectors[i:j], metadatas=metadatas[i:j],
        )
        logger.info("已写入 %d/%d 块", j, total)
    logger.info("已写入 %d 块到 %s", total, settings.CHROMA_COLLECTION)

    # v2.0 大点 01：语料变了 → BM25 索引与磁盘缓存必须失效，否则检索会命中旧语料
    try:
        from src.ai.rag import invalidate_corpus_cache  # lazy，避免循环 import

        invalidate_corpus_cache()
    except Exception as exc:  # noqa: BLE001
        logger.warning("BM25 语料缓存失效失败: %s", exc)
    return total


def _clean_for_index(text: str) -> str:
    """入库前清链接（保留原文件不动）：链接/来源节不进向量语料。"""
    try:
        from src.services.kb_govern import clean_for_index

        return clean_for_index(text)
    except Exception:  # noqa: BLE001 —— 清洗失败不阻断入库
        logger.warning("索引前清链接失败，按原文入库", exc_info=True)
        return text


def upsert_file(
    path: str | Path,
    doc_type: str,
    *,
    stem: str | None = None,
    source: str | None = None,
    extra_meta: dict | None = None,
) -> int:
    """单文件入库（v2.0 大点 04）：分块口径与 `build_from_dir` 完全一致。

    - `doc` 默认取文件名 stem（贡献落盘后即"最终 md 文件名"）；
    - `source` 默认 `knowledge_src/{doc_type}/{文件名}`（相对 backend/，正斜杠）；
    - `extra_meta` 会并入每个子块的 metadata（如 contrib_id / origin），供引用角标与追溯。
    """
    src = Path(path)
    if doc_type not in SPLITTERS:
        raise ValueError(f"未知 doc_type: {doc_type}（可选 {list(SPLITTERS)}）")
    text = _clean_for_index(src.read_text(encoding="utf-8"))
    doc_name = stem or src.stem
    src_rel = source or str(Path(settings.KNOWLEDGE_SRC_DIR) / doc_type / src.name).replace("\\", "/")
    chunks = SPLITTERS[doc_type](text, doc=doc_name, source=src_rel)
    if extra_meta:
        for c in chunks:
            c.setdefault("metadata", {}).update(extra_meta)
    n = upsert_chunks(chunks)
    logger.info("单文件入库 %s → doc=%s（%d 块，source=%s）", src.name, doc_name, n, src_rel)
    return n


def build_from_dir(src_dir: str | Path | None = None, doc_type: str | None = None) -> dict:
    """扫描知识库源目录（四类子目录的 .md/.txt），分块→嵌入→入库。

    目录约定（settings.KNOWLEDGE_SRC_DIR）:
        knowledge_src/
        ├── laws/        法规/制度（.md/.txt）
        ├── procedures/  操作规程
        ├── plans/       应急预案
        └── cases/       事故案例

    doc_type 非空时只处理指定类型（增量入库）；跳过 <2000 字节的文件
    （D3 数据治理标为 drop 的页面，如模板占位页）。

    性能：全部分块后**批量嵌入 + 一次 upsert**（而非逐文件调用），
    避免 83 次模型加载/调用开销；实测单文件流程 55s（含模型加载），
    批量后总量 2-4 分钟。

    返回 {"total_chunks": n, "by_type": {...}}
    """
    src = Path(src_dir or settings.KNOWLEDGE_SRC_DIR)
    if not src.exists():
        raise FileNotFoundError(f"知识库源目录不存在: {src}（请先创建四类子目录并放入文档）")

    all_chunks: list[dict] = []
    by_type: dict[str, int] = {}
    targets = [doc_type] if doc_type else list(SPLITTERS)
    for sub in targets:
        splitter = SPLITTERS.get(sub)
        if splitter is None:
            logger.warning("未知 doc_type: %s（可选 laws/cases/plans/procedures）", sub)
            continue
        sub_dir = src / sub
        if not sub_dir.is_dir():
            continue
        files = sorted(sub_dir.glob("*.md")) + sorted(sub_dir.glob("*.txt"))
        for f in files:
            size = f.stat().st_size
            min_bytes = getattr(settings, "KB_BUILD_MIN_BYTES", 2000)
            if size < min_bytes:
                logger.info("  跳过（治理标记 drop/过小）: %s (%dB < %dB)", f.name, size, min_bytes)
                continue
            text = _clean_for_index(f.read_text(encoding="utf-8"))
            chunks = splitter(text, doc=f.stem, source=str(f))
            all_chunks.extend(chunks)
            by_type[sub] = by_type.get(sub, 0) + len(chunks)
            print(f"  [{sub}] {f.name}: {len(chunks)} 块（累计 {len(all_chunks)}）", flush=True)
    total = upsert_chunks(all_chunks)
    return {"total_chunks": total, "by_type": by_type}
