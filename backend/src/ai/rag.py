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
import pickle
import threading
import time
from functools import lru_cache
from pathlib import Path

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

def _corpus_cache_file() -> Path:
    """BM25 语料磁盘缓存路径（与 chroma 持久化目录同级，.gitignore 已排除 chroma/ 与 *.pkl）。"""
    return Path(settings.CHROMA_DIR).resolve().parent / "bm25_corpus.pkl"


@lru_cache
def _load_corpus():
    """BM25 语料 = 全部子块文本 + id（与 Chroma 集合同源）。

    v2.0 大点 01 冷启动优化（实测）：
    - 旧实现 `col.get(include=["documents","metadatas"])` 会把每个子块的 **parent_text**
      （p50 ≈ 117K 字符）一起拉进内存，17,797 块 ≈ 2.2GB，耗时 **159.8s**，还写出 2.2GB pickle；
    - 现在只读 documents（子块文本，MB 级），metadata（含 parent_text）**按需**取：
      稀疏检索只回 30 条命中，用 id 回查这 30 条的 metadata 即可；
    - 语料仍落磁盘缓存（ids+corpus，块数校验），`upsert_chunks` 会主动失效。
    """
    from src.ai.knowledge import get_collection  # lazy

    col = get_collection()
    try:
        count = int(col.count())
    except Exception:  # noqa: BLE001 —— count 失败就当缓存不可信
        count = -1

    cache = _corpus_cache_file()
    if count >= 0 and cache.exists():
        try:
            obj = pickle.loads(cache.read_bytes())
            if obj.get("count") == count and obj.get("corpus"):
                logger.info(
                    "BM25 语料命中磁盘缓存: %d 块（免去 Chroma 全量读取）", len(obj["corpus"])
                )
                return obj["ids"], obj["corpus"]
            logger.info("BM25 语料缓存失效(%s != %s)，回源 Chroma", obj.get("count"), count)
        except Exception as exc:  # noqa: BLE001 —— 缓存坏了就当没有
            logger.warning("BM25 语料缓存读取失败，回源 Chroma: %s", exc)

    t0 = time.perf_counter()
    data = col.get(include=["documents"])
    ids = list(data["ids"])
    corpus = [d or "" for d in data["documents"]]
    logger.info("BM25 语料已从 Chroma 加载: %d 块 %.1fs", len(corpus), time.perf_counter() - t0)

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps({"count": count, "ids": ids, "corpus": corpus}, protocol=4))
        logger.info("BM25 语料已写入磁盘缓存: %s", cache)
    except Exception as exc:  # noqa: BLE001 —— 写缓存失败不影响检索
        logger.warning("BM25 语料缓存写入失败: %s", exc)
    return ids, corpus


@lru_cache
def _get_bm25():
    """lazy 构建 BM25Okapi 索引（进程内缓存，避免每次检索重建）。"""
    from rank_bm25 import BM25Okapi  # lazy

    _ids, corpus = _load_corpus()
    t0 = time.perf_counter()
    index = BM25Okapi([_tokenize(c) for c in corpus])
    logger.info("BM25 索引构建完成 %d 块 %.1fs", len(corpus), time.perf_counter() - t0)
    return index


def _hydrate_metadata(hits: list[dict]) -> list[dict]:
    """按 id 回查命中块的 metadata（含 parent_text），一次批量取。"""
    missing = [h for h in hits if not h.get("metadata") and h.get("id")]
    if not missing:
        return hits
    try:
        from src.ai.knowledge import get_collection  # lazy

        data = get_collection().get(ids=[h["id"] for h in missing], include=["metadatas"])
        by_id = dict(zip(data["ids"], data["metadatas"] or []))
        for h in missing:
            h["metadata"] = by_id.get(h["id"]) or {}
    except Exception as exc:  # noqa: BLE001 —— 取不到就退回纯文本命中
        logger.warning("稀疏命中 metadata 回查失败: %s", exc)
    return hits


def invalidate_corpus_cache() -> None:
    """知识库写入后调用：清进程内索引 + 删磁盘缓存，下次检索自动重建。"""
    _load_corpus.cache_clear()
    _get_bm25.cache_clear()
    try:
        _corpus_cache_file().unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning("删除 BM25 语料缓存失败: %s", exc)


_warm_lock = threading.Lock()
_warm_state: dict = {}
_warm_started = threading.Event()   # 预热已开始（检索端据此决定要不要等）
_warm_event = threading.Event()     # 预热已结束（成功或失败都置位）


def _await_warmup(timeout: float = 180.0) -> None:
    """检索若在预热期间到达，就等预热结束再跑。

    为什么不让它自己去算：lru_cache 不防并发，两个线程会各建一遍 BM25/各加载一份模型，
    实测「预热中提问」把首问检索从 13s 拖到 94s。等一次只多等预热剩余时间。
    """
    if _warm_event.is_set() or not _warm_started.is_set():
        return
    logger.info("检索请求等待预热完成…")
    if not _warm_event.wait(timeout):
        logger.warning("等待预热超时(%.0fs)，直接检索", timeout)


def warmup() -> dict:
    """预热检索链路（启动后台线程调用）：语料 → BM25 → embedding → 重排模型。

    目的：把「首问 175s 冷启动」挪到服务启动阶段，用户的第一问就按热态算。
    任何一步失败都只记日志，不影响服务可用性（后续检索会自行 lazy 加载）。
    """
    if _warm_state.get("done"):
        return _warm_state
    with _warm_lock:
        if _warm_state.get("done"):
            return _warm_state
        _warm_started.set()
        state: dict = {}
        t_all = time.perf_counter()
        try:
            t = time.perf_counter()
            _load_corpus()
            state["corpus_s"] = round(time.perf_counter() - t, 1)

            t = time.perf_counter()
            _get_bm25()
            state["bm25_s"] = round(time.perf_counter() - t, 1)

            try:
                t = time.perf_counter()
                from src.ai.embeddings import get_embedding_model  # lazy

                get_embedding_model().embed_query("预热")
                state["embedding_s"] = round(time.perf_counter() - t, 1)
            except Exception as exc:  # noqa: BLE001
                logger.warning("预热 embedding 失败: %s", exc)

            try:
                t = time.perf_counter()
                from src.ai.reranker import reranker  # lazy

                reranker.model
                state["reranker_s"] = round(time.perf_counter() - t, 1)
            except Exception as exc:  # noqa: BLE001
                logger.warning("预热重排模型失败: %s", exc)

            state["total_s"] = round(time.perf_counter() - t_all, 1)
            state["done"] = True
            _warm_state.update(state)
            logger.info("检索链路预热完成: %s", state)
        finally:
            _warm_event.set()   # 即使失败也放行检索
        return _warm_state


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
    ids, corpus = _load_corpus()
    scores = bm25.get_scores(_tokenize(question))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:SPARSE_K]
    hits = [
        {"text": corpus[i], "id": ids[i], "metadata": {}, "bm25_score": float(s)}
        for i, s in ranked
        if s > 0
    ]
    return _hydrate_metadata(hits)  # 只回查这 ≤30 条的 metadata（含 parent_text）


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
    _await_warmup()  # 预热进行中就先等它，避免重复建索引/重复加载模型
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
                          topics: list[str] | None = None
                          ) -> tuple[list[dict], list[int], dict[int, float]]:
    """v3.0：多子查询独立检索 + 文档级聚合保底（v2.0 §1.1 通用化）。

    每个子查询独立走 稠密+稀疏→RRF→重排，得到各自候选；再按 `doc` 聚合，
    保证"每个子问题至少贡献 1 份文档"，最后合并候选交文档配额精选。

    queries: 子查询列表（LLM 改写 或 零 LLM 关键词切分）；失败退化单查询。
    topics: v4.0 query 主题标签，用于主题一致性加权（空则跳过）。

    返回 (all_candidates, mmr_idx, score_map)：
    - score_map: 全局候选索引 → 该候选的最高重排分（v2.0 大点 01 新增，
      用于把命中块连分数一起写进会话缓存，命中时可直接复用而不必重排）。
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
        return [], [], {}

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

    # 全局候选 → 最高重排分（供会话缓存记录，命中时无需重排）
    score_map: dict[int, float] = {}
    for gi, score in global_pairs:
        if score > score_map.get(gi, float("-inf")):
            score_map[gi] = float(score)

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
    return all_candidates, mmr_idx, score_map


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
    candidates, mmr_idx, _scores = _multi_query_retrieve(queries, k, topics=q_topics)
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


# ---- v2.0 大点 01：子块级检索 + 预算裁剪（供会话缓存与增量补检复用）----

def retrieve_queries(
    queries: list[str], k: int | None = None, topics: list[str] | None = None
) -> list[dict]:
    """多子查询检索，返回**子块级**命中（带重排分），不直接拼父块。

    与 hybrid_retrieve 的区别：那边直接产出「父块拼好的 context」；这里返回结构化子块，
    供 chat 编排层写会话缓存、做去重合并、以及按预算装配上下文。

    返回：[{text, metadata, rerank_score}]，顺序 = 文档配额精选后的顺序。
    """
    if not queries:
        return []
    kk = k or settings.RAG_TOP_K_RERANK
    candidates, mmr_idx, score_map = _multi_query_retrieve(queries, kk, topics=topics)
    if not candidates:
        return []
    out: list[dict] = []
    for gi in mmr_idx:
        hit = dict(candidates[gi])
        hit["rerank_score"] = score_map.get(gi)
        out.append(hit)
    return out


def _cropped_block_text(hit: dict, budget: int) -> str:
    """单块文本：优先「父块中围绕命中子块的窗口」，找不到则退回子块原文。

    为什么不用父块全文：父块 p50 = 117,312 字符（实测），4 块就是 20 万字，
    生成阶段光读上下文就要几十秒。这里按预算取窗口，既保留语境又不顶爆。
    """
    child = (hit.get("text") or "").strip()
    parent = ((hit.get("metadata") or {}).get("parent_text") or "").strip()
    if not parent:
        return child[:budget]
    if child and child in parent:
        i = parent.find(child)
        half = max(0, (budget - len(child)) // 2)
        start = max(0, i - half)
        seg = parent[start : start + budget]
        prefix = "…" if start > 0 else ""
        suffix = "…" if start + budget < len(parent) else ""
        return f"{prefix}{seg}{suffix}"
    return parent[:budget]


def format_context(
    blocks: list[dict], budget_chars: int | None = None
) -> tuple[str, list[dict]]:
    """把子块命中装配成喂模型的 context（带 [来源: 文档#章节] 标注）。

    budget_chars：总字符预算（默认 settings.RAG_CONTEXT_BUDGET_CHARS）；
                  各块均分，单块至少 300 字符，避免块多时被压成一句话。
    返回 (context, sources)，sources 供 A06 引用落库，结构与原 hybrid_retrieve 一致。
    """
    if not blocks:
        return "", []
    budget = budget_chars or settings.RAG_CONTEXT_BUDGET_CHARS
    per_block = max(300, budget // max(1, len(blocks)))

    parts: list[str] = []
    sources: list[dict] = []
    seen_parent: set[str] = set()
    for hit in blocks:
        meta = hit.get("metadata") or {}
        parent_key = (meta.get("parent_text") or hit.get("text") or "")[:200]
        if not parent_key or parent_key in seen_parent:
            continue  # 同一父块的多个子块只保留一次
        seen_parent.add(parent_key)

        label = meta.get("doc", "未知文档")
        if meta.get("chapter"):
            label += f"#{meta['chapter']}"
        elif meta.get("article_no"):
            label += f"#{meta['article_no']}"
        parts.append(f"[来源: {label}]\n{_cropped_block_text(hit, per_block)}")
        sources.append(
            {
                "doc": meta.get("doc", ""),
                "doc_type": meta.get("doc_type", ""),
                "chapter": meta.get("chapter", ""),
                "article_no": meta.get("article_no", ""),
                "source": meta.get("source", ""),
            }
        )
    return "\n\n".join(parts), sources
