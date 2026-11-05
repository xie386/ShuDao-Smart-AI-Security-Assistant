"""隐患识别 Agent（v2.0 大点 06 · 裁定 8/9/10）。

**B 混合编排**：视觉识别是确定性一步，这里负责「给每条隐患写有依据的建议」。
拆成三个可独立调用的步骤，便于 SSE 分阶段推送与单测：

```
retrieve_all(hazards)   逐条构造检索式 → 并发 RAG（8s 超时/条）→ 返回 kb/scores/gaps
fill_gap(hazards, gaps) 缺口合并成 1 次网搜（agent_scout.run_sync，60s 预算）→ md + hosts
synthesize(...)         一次 LLM 汇总：每条 suggestion + refs（kb: doc#chapter / web: host）
enrich(hazards)         上面三步的组合（非流式路径用）
```

设计取舍（为什么不是"全 Agent 自主循环"）：步骤本身是确定的（看图 → 逐条查 → 汇总），
让 LLM 决定查几次只会带来抖动；工具面（`search_kb` / `report_knowledge_gap`）与补料闭环才是 Agent 的实质。

引用契约（裁定 10）：
- 库内：`{"kind":"kb","doc":..,"chapter":..}`
- 网络：`{"kind":"web","host":..}`
- 都没有：`suggestion=""`、`grounded=false`、`gap_note` 说明原因；**绝不编造**。
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.config import settings

logger = logging.getLogger(__name__)

GAP_THRESHOLD = 0.65          # 实测：有料 0.88~0.92，无料 0.54~0.61
RAG_TIMEOUT_S = 8.0           # 单条检索超时（温态 1~3s）
MAX_BLOCKS_PER_HAZARD = 2     # 每条的证据块数
BLOCK_CHARS = 300             # 每块截断
_MAX_CONCURRENT = 4


# --------------------------------------------------------------------------- #
# 可替换依赖（单测 monkeypatch 这几个）
# --------------------------------------------------------------------------- #
def _retrieve(query: str) -> list[dict]:
    from src.ai.rag import retrieve_queries

    return retrieve_queries([query]) or []


def _web_search(question: str) -> dict:
    """缺口补料：复用 04 的同步采集（同时提交知识文件审核）。"""
    from src.ai import agent_scout

    return agent_scout.run_sync(None, question)


def _synthesize(payload: dict) -> str:
    from src.ai.llm_client import llm_client
    from src.ai.prompts import render

    prompt = render(
        "hazard_suggest",
        hazards_json=json.dumps(payload["hazards"], ensure_ascii=False),
        kb_json=json.dumps(payload["kb"], ensure_ascii=False),
        web_hosts=", ".join(payload["web_hosts"]) or "（无）",
        web_md=(payload["web_md"] or "（无）")[:6000],
    )
    return llm_client.chat([{"role": "system", "content": prompt}], temperature=0.2) or ""


# --------------------------------------------------------------------------- #
# 步骤 1：并发检索
# --------------------------------------------------------------------------- #
def _query_of(h: dict) -> str:
    desc = (h.get("description") or "").strip()
    cat = (h.get("category") or "").strip()
    return f"{desc} {cat} 整改 要求".strip()[:80]


def _top_score(blocks: list[dict]) -> float:
    if not blocks:
        return 0.0
    top = blocks[0]
    try:
        return float(top.get("rerank_score", top.get("score")) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _trim_blocks(blocks: list[dict]) -> list[dict]:
    out = []
    for b in blocks[:MAX_BLOCKS_PER_HAZARD]:
        m = b.get("metadata") or {}
        try:
            score = round(float(b.get("rerank_score", b.get("score") or 0) or 0), 3)
        except (TypeError, ValueError):
            score = 0.0
        out.append(
            {
                "doc": m.get("doc") or "",
                "chapter": (m.get("chapter") or "")[:24],
                "score": score,
                "text": (b.get("text") or "").replace("\n", " ")[:BLOCK_CHARS],
            }
        )
    return out


def retrieve_all(hazards: list[dict]) -> dict:
    """并发检索每条隐患。返回 {kb, scores, gaps, queries, secs}。"""
    t0 = time.perf_counter()
    items = hazards or []
    queries = [_query_of(h) for h in items]
    kb: dict[int, list[dict]] = {}
    scores: dict[int, float] = {}

    if items:
        with ThreadPoolExecutor(max_workers=min(_MAX_CONCURRENT, len(items))) as pool:
            futures = {pool.submit(_retrieve, q): i for i, q in enumerate(queries, start=1)}
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    blocks = fut.result(timeout=RAG_TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001 —— 单条失败按"无证据"处理
                    logger.warning("隐患 %s 检索失败：%s", idx, exc)
                    blocks = []
                kb[idx] = _trim_blocks(blocks)
                scores[idx] = _top_score(blocks)

    gaps = [i for i in range(1, len(items) + 1) if scores.get(i, 0.0) < GAP_THRESHOLD]
    logger.info("隐患检索：%d 条 / %s / 缺口 %s", len(items), {k: round(v, 3) for k, v in scores.items()}, gaps)
    return {
        "kb": kb,
        "scores": scores,
        "gaps": gaps,
        "queries": queries,
        "secs": round(time.perf_counter() - t0, 1),
    }


# --------------------------------------------------------------------------- #
# 步骤 2：缺口补料（一次合并）
# --------------------------------------------------------------------------- #
def _merge_gap_question(hazards: list[dict], gaps: list[int]) -> str:
    parts = [(hazards[i - 1].get("description") or "").strip() for i in gaps]
    parts = [p for p in parts if p]
    return ("；".join(parts)[:120] or "施工现场安全隐患") + " 整改措施 规范 依据"


def fill_gap(hazards: list[dict], gaps: list[int]) -> dict:
    """缺口 → 同步网搜（同时提交知识文件审核）。返回 {ok, md, hosts, contrib_id, note, secs}。"""
    if not gaps:
        return {"ok": False, "md": "", "hosts": [], "note": "", "skipped": True, "secs": 0.0}
    if not getattr(settings, "HAZARD_WEB_FILL_ENABLED", True):
        return {"ok": False, "md": "", "hosts": [], "note": "网搜已关闭", "secs": 0.0}

    question = _merge_gap_question(hazards, gaps)
    t0 = time.perf_counter()
    # 硬上限：`run_sync` 内部只约束"采集"，LLM 总结还能再拖 40s（真机实测 105s）。
    # 这里用独立线程卡总时长，超时就放弃网搜、按"无依据"返回，不拖垮整次识别。
    limit = float(getattr(settings, "HAZARD_WEB_TIMEOUT", 70.0) or 70.0)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_web_search, question)
            res = fut.result(timeout=limit) or {}
    except TimeoutError as exc:
        # 3.11 起 concurrent.futures.TimeoutError 就是内置 TimeoutError，按耗时区分
        if time.perf_counter() - t0 >= limit - 0.05:
            logger.warning("缺口网搜超过 %.0fs，放弃本轮（识别继续）", limit)
            res = {"ok": False, "note": f"联网补充超时（>{limit:.0f}s）"}
        else:
            logger.warning("缺口网搜自身超时：%s", exc)
            res = {"ok": False, "note": f"TimeoutError: {exc}"[:80]}
    except Exception as exc:  # noqa: BLE001 —— 网搜失败不能拖垮识别
        logger.warning("缺口网搜异常：%s", exc)
        res = {"ok": False, "note": f"{type(exc).__name__}: {exc}"[:80]}
    secs = round(time.perf_counter() - t0, 1)
    if res.get("ok"):
        out = {"ok": True, "md": res.get("md") or "", "hosts": list(res.get("domains") or []),
               "contrib_id": res.get("contrib_id"), "note": "", "secs": secs, "question": question}
        logger.info("缺口网搜命中：%d 字 / %s / 贡献 %s / %.0fs",
                    len(out["md"]), out["hosts"], out.get("contrib_id"), secs)
        return out
    out = {"ok": False, "md": "", "hosts": [], "note": res.get("note") or "网搜未成", "secs": secs,
           "question": question}
    logger.info("缺口网搜未成：%s（%.0fs）", out["note"], secs)
    return out


# --------------------------------------------------------------------------- #
# 步骤 3：汇总建议（带引用校验）
# --------------------------------------------------------------------------- #
def _parse_suggestions(raw: str) -> dict[int, dict]:
    text = (raw or "").strip()
    if not text:
        return {}
    start, end = text.find("["), text.rfind("]")
    data = None
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            data = None
    if data is None:
        s2, e2 = text.find("{"), text.rfind("}")
        if s2 >= 0 and e2 > s2:
            try:
                obj = json.loads(text[s2:e2 + 1])
                data = obj if isinstance(obj, list) else (obj.get("items") or obj.get("hazards"))
            except json.JSONDecodeError:
                data = None
    out: dict[int, dict] = {}
    for item in data or []:
        if not isinstance(item, dict):
            continue
        try:
            out[int(item.get("index"))] = item
        except (TypeError, ValueError):
            continue
    return out


def _clean_refs(refs, kb_blocks: list[dict], web_hosts: set[str]) -> list[dict]:
    """只保留能对上证据的引用；模型编的 doc/host 一律丢弃。"""
    kb_pairs = {((b.get("doc") or ""), (b.get("chapter") or "")) for b in kb_blocks}
    kb_docs = {b.get("doc") for b in kb_blocks if b.get("doc")}
    out: list[dict] = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        kind = (r.get("kind") or "").lower()
        if kind == "kb":
            doc, chapter = (r.get("doc") or ""), (r.get("chapter") or "")
            if (doc, chapter) in kb_pairs or (doc in kb_docs and not chapter):
                out.append({"kind": "kb", "doc": doc, "chapter": chapter})
        elif kind == "web":
            host = (r.get("host") or "").strip().lower()
            if host and any(host == h or h.endswith(host) or host.endswith(h) for h in web_hosts):
                out.append({"kind": "web", "host": host})
    seen, uniq = set(), []
    for r in out:
        key = (r["kind"], r.get("doc"), r.get("chapter"), r.get("host"))
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq[:2]


def synthesize(hazards: list[dict], kb: dict[int, list[dict]], web: dict | None) -> dict:
    """一次 LLM 汇总 → 返回补好 suggestion/refs/grounded 的 hazards（原地不修改入参）。"""
    items = [dict(h) for h in (hazards or [])]
    web = web or {}
    web_md = web.get("md") or ""
    web_hosts = list(web.get("hosts") or [])
    gap_note_default = "知识库暂无该类隐患依据"

    payload = {
        "hazards": [
            {"index": i, "level": h.get("level"), "category": h.get("category"),
             "description": h.get("description")}
            for i, h in enumerate(items, start=1)
        ],
        "kb": {str(k): v for k, v in (kb or {}).items()},
        "web_hosts": web_hosts,
        "web_md": web_md,
    }
    t0 = time.perf_counter()
    parsed: dict[int, dict] = {}
    try:
        parsed = _parse_suggestions(_synthesize(payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning("建议汇总失败：%s", exc)

    valid_hosts = set(web_hosts)
    for i, h in enumerate(items, start=1):
        sug = parsed.get(i) or {}
        suggestion = (sug.get("suggestion") or "").strip()
        kb_blocks = (kb or {}).get(i) or []
        refs = _clean_refs(sug.get("refs"), kb_blocks, valid_hosts)
        # 模型说"有依据"且它引用的**文档确实在检索结果里**、只是章节名写法不一致 →
        # 回落到该条 Top1 块（真机 #5 踩过：0.88 的证据被引用写法丢掉）。
        # 反过来，若它引的文档是检索结果里没有的（编造），一律不信，保持无依据。
        kb_docs = {b.get("doc") for b in kb_blocks if b.get("doc")}
        cited_docs = {r.get("doc") for r in (sug.get("refs") or []) if isinstance(r, dict) and (r.get("kind") or "").lower() == "kb"}
        if suggestion and not refs and kb_blocks and sug.get("grounded") and (cited_docs & kb_docs):
            top = kb_blocks[0]
            if top.get("doc"):
                refs = [{"kind": "kb", "doc": top["doc"], "chapter": top.get("chapter") or ""}]
        grounded = bool(sug.get("grounded")) and bool(suggestion) and bool(refs)
        if sug.get("grounded") and not grounded:
            logger.info("隐患 %s 建议降级：refs=%s suggestion=%s", i, sug.get("refs"), suggestion[:40])
        h["suggestion"] = suggestion if grounded else ""
        h["refs"] = refs if grounded else []
        h["grounded"] = grounded
        h["gap_note"] = (sug.get("gap_note") or "").strip() or (
            "" if grounded else (f"网搜未成（{web.get('note')}）" if (web.get("note") and not web_md)
                                 else gap_note_default)
        )
    logger.info("建议汇总：%d 条（llm %.1fs）", len(items), time.perf_counter() - t0)
    return {"hazards": items, "llm_s": round(time.perf_counter() - t0, 1)}


def enrich(hazards: list[dict], *, conversation_id: int | None = None,
           enable_web: bool = True) -> dict:
    """非流式路径：三步一次跑完。"""
    t0 = time.perf_counter()
    items = hazards or []
    if not items:
        return {"hazards": [], "gap": None, "meta": {"reason": "no_hazards"}, "elapsed_s": 0.0}

    rag = retrieve_all(items)
    web = fill_gap(items, rag["gaps"]) if enable_web else {"ok": False, "md": "", "hosts": [],
                                                           "note": "网搜已关闭", "secs": 0.0}
    syn = synthesize(items, rag["kb"], web)
    return {
        "hazards": syn["hazards"],
        "gap": {"indices": rag["gaps"], "web": {k: v for k, v in web.items() if k != "md"}},
        "meta": {
            "model": settings.DEEPSEEK_MODEL,
            "scores": {str(k): round(v, 3) for k, v in rag["scores"].items()},
            "gap_threshold": GAP_THRESHOLD,
            "rag_s": rag["secs"],
            "llm_s": syn["llm_s"],
        },
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
