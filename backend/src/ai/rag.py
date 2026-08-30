"""RAG 混合检索编排（AI_SOLUTION §5.1–5.4）。

流程：稠密检索(Chroma/bge-large-zh) + 稀疏检索(BM25/jieba 分词)
     → RRF 融合 → bge-reranker 重排 → 父块注入(metadata.parent_text) → 返回带来源的上下文。

设计要点：
- 写入侧走 chromadb 直连(get_collection)，检索侧稠密走 langchain_chroma(get_vectorstore)，
  稀疏基于 Chroma 全量文档构建 BM25 索引（lazy，首次检索时构建并缓存）；
- 父子分块(§4.3)：向量命中"子块"，注入 LLM 的是"父块"完整文本（parent_text），既搜得准又给全语境；
- 输出 context 带 [来源: 文档#章节] 标注（呼应 A06 回答引用），sources 供落库 ChatMessage.refs；
- 查询改写/HyDE(§5.4) 属 P3 优化，本期留 TODO（见文件尾）。

用法：
    from src.ai.rag import hybrid_retrieve
    context, sources = hybrid_retrieve("高处作业有哪些要求")
"""
import logging
from functools import lru_cache

from src.core.config import settings

logger = logging.getLogger(__name__)

DENSE_K = 30     # 稠密初召回（B2 调优：20→30，cases 类长尾召回）
SPARSE_K = 30    # 稀疏初召回（B2 调优：20→30）
FUSE_TOPK = 30   # RRF 融合后取前 N 进重排（B2 调优：20→30，给重排更多候选）
RRF_K = 60       # RRF 平滑常数


# ---- 工具 ----

def _tokenize(text: str) -> list[str]:
    """jieba 中文分词（过滤空白与单字符噪声）。"""
    import jieba  # lazy

    return [t for t in jieba.lcut(text) if t.strip() and len(t.strip()) > 1]


# ---- 索引缓存 ----

@lru_cache
def _load_corpus():
    """全量文档 + 元数据缓存（BM25 语料，与 Chroma 集合同源）。"""
    from src.ai.knowledge import get_collection  # lazy

    col = get_collection()
    data = col.get(include=["documents", "metadatas"])
    corpus = [d or "" for d in data["documents"]]
    metas = data["metadatas"] or [{}] * len(corpus)
    logger.info("BM25 语料已加载: %d 块", len(corpus))
    return corpus, metas


@lru_cache
def _get_bm25():
    """lazy 构建 BM25Okapi 索引（进程内缓存，避免每次检索重建）。"""
    from rank_bm25 import BM25Okapi  # lazy

    corpus, _ = _load_corpus()
    return BM25Okapi([_tokenize(c) for c in corpus])


# ---- 两路召回 ----

def _dense_search(question: str) -> list[dict]:
    """稠密检索：LangChain 向量检索 Top-K。"""
    from src.ai.knowledge import get_vectorstore  # lazy

    retriever = get_vectorstore().as_retriever(search_kwargs={"k": DENSE_K})
    docs = retriever.invoke(question)
    return [
        {
            "text": d.page_content,
            "metadata": d.metadata or {},
        }
        for d in docs
    ]


def _sparse_search(question: str) -> list[dict]:
    """稀疏检索：BM25 关键词 Top-K（条款号/专业术语精准命中）。"""
    bm25 = _get_bm25()
    corpus, metas = _load_corpus()
    scores = bm25.get_scores(_tokenize(question))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:SPARSE_K]
    return [
        {"text": corpus[i], "metadata": metas[i] or {}, "bm25_score": float(s)}
        for i, s in ranked
        if s > 0
    ]


def _rrf_fuse(dense: list[dict], sparse: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion：两路排序融合（1/(K+rank) 求和）。"""
    scores: dict[str, float] = {}
    items: dict[str, dict] = {}
    for rank, hit in enumerate(dense):
        key = hit["text"]
        scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        items[key] = hit
    for rank, hit in enumerate(sparse):
        key = hit["text"]
        scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        items.setdefault(key, hit)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:FUSE_TOPK]
    return [items[key] for key, _ in fused]


def _retrieve_candidates(question: str) -> list[dict]:
    """单查询的两路召回 + RRF 融合（不重排，供多路合并）。"""
    dense = _dense_search(question)
    if not dense:
        return []
    return _rrf_fuse(dense, _sparse_search(question))


# ---- MMR 多样性重排（v3.0：文档级配额，替代 v2.0 的文本相似度惩罚）----

def _doc_of(hit: dict) -> str:
    """候选块的文档身份（metadata.doc）。"""
    return hit.get("metadata", {}).get("doc", "")


def _parent_of(hit: dict) -> str:
    """父块身份：去重与排序的稳定键。"""
    return (hit.get("metadata", {}).get("parent_text") or hit["text"]).strip()


def _mmr_select(candidates: list[dict], rerank_pairs: list[tuple[int, float]],
                k: int, doc_cap: int | None = None) -> list[int]:
    """文档级配额精选（v3.0 改动 1），返回选中的候选索引。

    v2.0 用 bge 句向量余弦惩罚"文本相似的块"是错的：应急预案/法规的结构化文本
    天然高度相似（如国家预案 vs 各地地方预案），文本相似 ≠ 内容重复，导致
    精确率 0.25→0.14 崩盘（90/200 题恰好 0.10）。v3.0 改为**文档级配额**：

    - 按 rerank 分降序依次入选；
    - **同一 doc 最多占 doc_cap 席**（默认 2，保留"章"级多样性），超限的块跳过；
    - **不同 doc 之间完全不惩罚**——同主题不同文档不再互相挤掉。

    复杂度 O(N log N)，N=FUSE_TOPK，可忽略。

    rerank_pairs: reranker 返回的 [(idx, score)]（降序）；k: 最终取几个父块。
    """
    if not rerank_pairs:
        return []
    # 父块去重，保留 rerank 最高分的那条子块代表该父块
    best_of_parent: dict[str, tuple[int, float]] = {}
    for idx, score in rerank_pairs:
        p = _parent_of(candidates[idx])
        cur = best_of_parent.get(p)
        if cur is None or score > cur[1]:
            best_of_parent[p] = (idx, score)
    # 按 rerank 分降序排列候选池
    pool = sorted(best_of_parent.values(), key=lambda x: x[1], reverse=True)
    if len(pool) <= k:
        return [idx for idx, _ in pool]

    cap = doc_cap if doc_cap is not None else settings.RAG_MMR_DOC_CAP
    selected: list[int] = []
    doc_count: dict[str, int] = {}
    for idx, _score in pool:
        if len(selected) >= k:
            break
        doc = _doc_of(candidates[idx])
        if doc_count.get(doc, 0) >= cap:
            continue  # 同 doc 已满配额，跳过（不同 doc 不惩罚）
        selected.append(idx)
        doc_count[doc] = doc_count.get(doc, 0) + 1
    # 极端情况：配额限制导致选不满 k（如全部候选同属一个 doc 且 cap 很小）
    if len(selected) < k:
        for idx, _score in pool:
            if len(selected) >= k:
                break
            if idx not in selected:
                selected.append(idx)
    return selected


def _keyword_split(question: str) -> list[str]:
    """零 LLM 关键词切分（--no-rewrite 评测路径用）。

    按顿号/逗号/和/与 拆复合问题为子查询，让多跳题在纯检索评测下
    也能走"多子查询独立检索 → 文档级聚合"。

    v8.x 改进（治多跳召回，实测根因）：
    1. 取全部片段（上限 5），不再截断前 3 个——四跳题有 4~5 个子问，
       截断会丢"规程/预案"子查询（实测 procedures/plans 召回被拖低）；
    2. 泛化片段继承主题词——"法规如何规定""事故教训"这类不含主题的片段，
       前缀第一个片段的主题核心，避免检索漂移（实测 laws 召回仅 0.17）。

    拆不出多个有效片段时退化为原问题单查询。
    """
    import re

    parts = [p.strip(" ？?。.!！") for p in re.split(r"[、，,;；和与]", question)]
    parts = [p for p in parts if len(p) >= 2]  # 滤掉单字碎片
    if len(parts) <= 1:
        return [question]

    topic = parts[0]
    # 主题核心 = 去掉"的XX管理/防范/治理"等通用后缀（"高处作业的安全管理"→"高处作业"）
    topic_core = re.sub(r"(的安全管理|的防范|的管理|的治理)$", "", topic).strip() or topic

    queries = [topic]
    for p in parts[1:]:
        # 泛化片段（不含主题核心）继承主题，避免检索漂移
        if topic_core not in p:
            p = f"{topic_core} {p}"
        queries.append(p)

    # 去重 + 上限 5（四跳最多 4~5 个子问）
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:5]


def _extract_query_topic(question: str) -> list[str]:
    """v4.0 query 主题识别：复用入库侧主题词典做规则匹配（零 LLM 成本）。

    返回 query 命中的主题标签列表（可能多个，如"校园火灾"→[校园, 消防]）；
    命中不到返回空列表（此时不做主题加权，退回 v3.0 行为）。
    """
    from src.ai.knowledge import _PLAN_TOPIC_TITLE  # lazy，避免循环 import

    return [topic for kws, topic in _PLAN_TOPIC_TITLE if any(kw in question for kw in kws)]


def _multi_query_retrieve(queries: list[str], k: int,
                          topics: list[str] | None = None) -> tuple[list[dict], list[int]]:
    """v3.0：多子查询独立检索 + 文档级聚合保底（v2.0 §1.1 通用化）。

    每个子查询独立走 稠密+稀疏→RRF→重排，得到各自候选；再按 `doc` 聚合，
    保证"每个子问题至少贡献 1 份文档"，最后合并候选交文档配额精选。

    queries: 子查询列表（LLM 改写 或 零 LLM 关键词切分）；失败退化单查询。
    topics: v4.0 query 主题标签，用于主题一致性加权（空则跳过）。
    """
    per_query: list[list[tuple[int, float]]] = []  # 每子查询的重排结果（相对各自候选池）
    all_candidates: list[dict] = []
    seen_text: set[str] = set()
    for q in queries:
        cands = _retrieve_candidates(q)
        if not cands:
            continue
        # 候选去重（跨子查询同文本不重复编码）
        uniq = []
        for h in cands:
            if h["text"] not in seen_text:
                seen_text.add(h["text"])
                uniq.append(h)
        if not uniq:
            continue
        all_candidates.extend(uniq)
        pairs = reranker_topk(q, [h["text"] for h in uniq], top_k=len(uniq))
        per_query.append(pairs)

    if not all_candidates:
        return [], []

    # 文档级聚合：把每个子查询 rerank Top-1 强制纳入，保多跳下限。
    # per_query 的 idx 相对各子查询自身 uniq，需映射到 all_candidates 全局偏移。
    global_pairs: list[tuple[int, float]] = []
    offset = 0
    forced_global: set[int] = set()
    for pairs in per_query:
        for rank, (local_idx, score) in enumerate(pairs):
            global_pairs.append((offset + local_idx, score))
            if rank == 0:  # 该子查询 rerank 第一
                forced_global.add(offset + local_idx)
        offset += len(pairs)

    # v4.0 主题一致性加权：匹配 query 主题的候选 rerank 分乘 1+α（只加权不惩罚，
    # 召回不降；匹配主题的期望文档排名提升 → 占更多席 → 精确率回升）
    if topics:
        boost = settings.RAG_TOPIC_BOOST
        weighted: list[tuple[int, float]] = []
        for gi, score in global_pairs:
            hit_tags = (all_candidates[gi].get("metadata", {}) or {}).get("tags") or []
            if any(t in hit_tags for t in topics):
                score = score * (1.0 + boost)
            weighted.append((gi, score))
        global_pairs = weighted

    # 全量候选过文档配额精选；forced（每个子查询的 Top1）优先保底
    mmr_idx = _mmr_select(all_candidates, global_pairs, k)
    # 保底：forced 里没进 mmr 的强行补进（不超过 k）
    for gi in forced_global:
        if gi not in mmr_idx and len(mmr_idx) < k:
            mmr_idx.append(gi)
    return all_candidates, mmr_idx


def reranker_topk(query: str, docs: list[str], top_k: int) -> list[tuple[int, float]]:
    """reranker.rerank 的透传封装（避免循环 import 时重复写）。"""
    from src.ai.reranker import reranker  # lazy

    return reranker.rerank(query, docs, top_k=top_k)


# ---- 查询改写（AI_SOLUTION §5.4）----

def _parse_json_array(content: str) -> list[str]:
    """宽容解析 LLM 返回的 JSON 数组（剥 ``` 围栏/杂质）。"""
    import json

    text = content.strip()
    if "```" in text:
        text = text.split("```")[1] if "```json" in text or text.startswith("```") else text
        text = text.strip("`")
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        arr = json.loads(text)
        return [str(x).strip() for x in arr if str(x).strip()]
    except Exception:
        return []


def rewrite_query(question: str) -> list[str]:
    """口语问题扩写为 2-3 个子查询（prompts.yml query_rewrite 场景）。

    失败时退化返回原问题（不阻断检索）；多一次 DeepSeek 调用（约 1-2s）。
    """
    from src.ai.llm_client import llm_client  # lazy
    from src.ai.prompts import render  # lazy

    system_prompt = render("query_rewrite", question=question)
    try:
        content = llm_client.chat(
            [{"role": "system", "content": system_prompt}], temperature=0.0
        )
        queries = _parse_json_array(content)
        return (queries or [question])[:3]
    except Exception as exc:  # noqa: BLE001
        logger.warning("查询改写失败(%s)，使用原问题", exc)
        return [question]


# ---- 对外入口 ----

def hybrid_retrieve(
    question: str, top_k: int | None = None, use_rewrite: bool | None = None
) -> tuple[str, list[dict]]:
    """混合检索 → 重排 → 文档级配额精选 → 父块注入。

    参数：
    - top_k：重排后取前 N 个父块（默认 settings.RAG_TOP_K_RERANK）；
    - use_rewrite：是否启用查询改写（默认 settings.RAG_QUERY_REWRITE）。
      改写开启 = LLM 多子查询分解；关闭（评测 --no-rewrite）= 零 LLM 关键词切分。
      两条路径都走"各子查询独立检索 → 文档级聚合保底 → 文档配额精选"（v3.0）。

    返回 (context, sources)：
    - context：拼接好的父块文本，每段带 [来源: 文档#章节] 标注，直接注入 safety_qa 模板；
    - sources：引用元数据列表 [{doc, doc_type, chapter, article_no, source}]，供 A06 落库。
    """
    k = top_k or settings.RAG_TOP_K_RERANK
    use_rw = settings.RAG_QUERY_REWRITE if use_rewrite is None else use_rewrite

    if use_rw:
        # 改写开启：LLM 分解为 2-3 个子查询（口语问题扩写）
        queries = rewrite_query(question)
    else:
        # --no-rewrite 评测路径：零 LLM 关键词切分（v3.0 改动 2，让多跳评测真正测到聚合）
        queries = _keyword_split(question)
    # v4.0 主题识别：对原问题做主题词典匹配，供检索侧主题加权（治 plans 同质化）
    q_topics = _extract_query_topic(question)
    candidates, mmr_idx = _multi_query_retrieve(queries, k, topics=q_topics)
    if not candidates:
        logger.warning("检索无候选（知识库为空？）")
        return "", []

    # 父块注入：按文档配额精选顺序取完整父块文本（去重，保持顺序）
    seen_parent: set[str] = set()
    context_parts: list[str] = []
    sources: list[dict] = []
    for idx in mmr_idx:
        hit = candidates[idx]
        meta = hit.get("metadata", {})
        parent = (meta.get("parent_text") or hit["text"]).strip()
        if not parent or parent in seen_parent:
            continue
        seen_parent.add(parent)
        label = f"{meta.get('doc', '未知文档')}"
        if meta.get("chapter"):
            label += f"#{meta['chapter']}"
        elif meta.get("article_no"):
            label += f"#{meta['article_no']}"
        context_parts.append(f"[来源: {label}]\n{parent}")
        sources.append(
            {
                "doc": meta.get("doc", ""),
                "doc_type": meta.get("doc_type", ""),
                "chapter": meta.get("chapter", ""),
                "article_no": meta.get("article_no", ""),
                "source": meta.get("source", ""),
            }
        )
    return "\n\n".join(context_parts), sources
