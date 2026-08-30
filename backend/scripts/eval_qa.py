#!/usr/bin/env python
"""golden QA 集评估：**RAG 六指标**，检索侧按跳数分组，生成侧 LLM 裁判（AI_SOLUTION §6）。

用法（在 backend/ 目录下，使用全局 Python）：
    # 检索三指标（纯计算，不调 LLM，200 题跑全量）
    PYTHONPATH= C:\\python\\python.exe scripts/eval_qa.py --no-rewrite
    PYTHONPATH= C:\\python\\python.exe scripts/eval_qa.py --no-rewrite --hop multi   # 只看 60 条多跳
    PYTHONPATH= C:\\python\\python.exe scripts/eval_qa.py --no-rewrite --fail-only   # 只打印失分题

    # 加生成三指标（每题要 1 次生成 + 2 次裁判 → 必须抽样！）
    PYTHONPATH= C:\\python\\python.exe scripts/eval_qa.py --no-rewrite --judge --limit 20

    # 单题排查
    PYTHONPATH= C:\\python\\python.exe scripts/eval_qa.py --question "自定义问题" --judge

⚠ 200 题必加 --no-rewrite：开改写会走 200 次 LLM，必定超时；改写效果属 P3 人工评分。
⚠ --judge 必须配 --limit：200 题 × 3 次 LLM = 600 次调用，烧额度且必然超时。

六指标（口径与实现见 scripts/rag_metrics.py 模块注释）：
    chunk_recall_at_k     chunk 级召回 @K    ← 期望文档被 chunk 覆盖比例
    chunk_precision_at_k  chunk 级精确率 @N  ← 注入深度前 N 块里多少来自期望文档（噪声；N 默认 4=线上注入深度）
    context_recall        上下文召回         ← 最终喂 LLM 的 context 覆盖了多少期望知识
    answer_correctness    答案正确性         ← 需 expected_answer 标注，**当前 N/A**
    faithfulness          忠实度             ← 答案是否被 context 支撑（幻觉检测）
    answer_relevance      答案相关性         ← 是否切题

QA 集格式（knowledge_src/golden_qa.jsonl，由 scripts/extend_qa.py 生成）：
    {"question": "...", "expected_docs": ["doc1","doc2"], "category": "cases", "hop": "double"}
    可选字段 "expected_answer": "参考答案文本" —— 有则 answer_correctness 生效，无则记 N/A。

跳数（hop）：single 1 份 / double 2 份 / triple 3 份 / quad 4 份（缺字段按数量推断）。
多跳题 recall 天然低（3 份命中 1 份仅 0.33），**必须分组看**，否则 60 条多跳会把总分
压低，让人误判成检索链路坏了。topk 默认 10；多跳题用 4 结构性不够。
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录 rag_metrics

import rag_metrics as M  # noqa: E402
from src.ai.rag import hybrid_retrieve  # noqa: E402

QA_FILE = Path(__file__).resolve().parent.parent / "knowledge_src" / "golden_qa.jsonl"

# 检索侧指标（纯计算） + 生成侧指标（LLM 裁判）
RETRIEVAL_KEYS = ["chunk_recall_at_k", "chunk_precision_at_k", "context_recall"]
GEN_KEYS = ["answer_correctness", "faithfulness", "answer_relevance"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=10,
                    help="检索深度。默认 10（对齐 P2 Recall@10 验收）；多跳题用 4 结构性不够")
    ap.add_argument("--precision-at", type=int, default=4,
                    help="精确率计算的注入深度，默认 4（对齐线上 RAG_TOP_K_RERANK=4）。"
                         "候选池 top-10 的精确率被文档配额 RAG_MMR_DOC_CAP=2 结构性压死"
                         "（single 题期望文档最多 2 席/10=0.20 天花板），"
                         "注入深度才是模型实际看到的噪声。传 10 可回到旧口径")
    ap.add_argument("--question", default="", help="单题快速评估（不读 QA 集）")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="关闭查询改写（测纯检索链路，P2 指标；改写属 P3 人工评分）")
    ap.add_argument("--hop", default="", choices=["", "single", "double", "triple", "quad"],
                    help="只评估指定跳数，便于单独排查多跳失分")
    ap.add_argument("--category", default="", help="只评估指定类别（laws/cases/plans/procedures）")
    ap.add_argument("--fail-only", action="store_true", help="只打印未全中的题，刷屏时用")
    ap.add_argument("--judge", action="store_true",
                    help="开启生成侧三指标（faithfulness / answer_relevance / answer_correctness）。"
                         "每题需 1 次生成 + 2~3 次裁判，务必配 --limit 抽样")
    ap.add_argument("--limit", type=int, default=0, help="只评估前 N 题（--judge 时强烈建议 20~30）")
    ap.add_argument("--json-out", default="", help="把逐题明细写入 JSON 文件，便于对比调参前后")
    args = ap.parse_args()

    if args.question:
        r = _eval_one({"question": args.question, "expected_docs": []}, args.topk,
                      use_rewrite=not args.no_rewrite, judge=args.judge,
                      precision_at=args.precision_at)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return

    if not QA_FILE.exists():
        print(f"QA 集不存在: {QA_FILE}（用 scripts/extend_qa.py 生成 200 题）")
        sys.exit(1)

    qas = [json.loads(l) for l in QA_FILE.open(encoding="utf-8") if l.strip()]
    if args.hop:
        qas = [q for q in qas if _hop(q) == args.hop]
    if args.category:
        qas = [q for q in qas if q.get("category") == args.category]
    if args.limit:
        qas = qas[: args.limit]
    if not qas:
        sys.exit("过滤后无题目可评估")

    mode = "改写关（纯检索）" if args.no_rewrite else "改写开"
    judge_note = "，生成侧 LLM 裁判：开" if args.judge else ""
    print(f"评估 {len(qas)} 题，top_k={args.topk}，{mode}{judge_note}\n")
    if args.judge and len(qas) > 40:
        print(f"⚠ 警告：--judge 下 {len(qas)} 题要发起约 {len(qas)*3} 次 LLM 调用，"
              f"极可能超时/烧额度。建议加 --limit 20\n")

    # 分 hop 累计。每个指标独立累计 n，因为 answer_correctness 可能整列 N/A。
    stats: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"n": 0, "sum": 0.0}))
    extra: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "full": 0, "hit": 0})
    details = []

    for qa in qas:
        r = _eval_one(qa, args.topk, use_rewrite=not args.no_rewrite,
                      judge=args.judge, quiet=args.fail_only,
                      precision_at=args.precision_at)
        details.append(r)
        recall = r["chunk_recall_at_k"]
        for key in (_hop(qa), "ALL"):
            e = extra[key]
            e["n"] += 1
            e["hit"] += 1 if recall > 0 else 0
            e["full"] += 1 if recall >= 1.0 else 0
            for m in RETRIEVAL_KEYS + GEN_KEYS:
                v = r.get(m)
                if isinstance(v, (int, float)):  # None（N/A / 裁判失败）不计入均值
                    stats[key][m]["n"] += 1
                    stats[key][m]["sum"] += v

    _print_summary(stats, extra, args)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n逐题明细已写入: {args.json_out}")


def _print_summary(stats, extra, args) -> None:
    print("\n===== 检索侧指标（分跳数） =====")
    hdr_recall = f"chunk召回@{args.topk}"
    hdr_prec = f"chunk精确@{args.precision_at}"
    print(f"{'跳数':<8}{'题数':>5}{hdr_recall:>13}{hdr_prec:>13}{'上下文召回':>12}{'命中率':>9}{'全中率':>9}")
    for key in ("single", "double", "triple", "quad", "ALL"):
        e = extra.get(key)
        if not e or not e["n"]:
            continue
        n = e["n"]
        cells = "".join(_cell(stats[key][m], 13) for m in RETRIEVAL_KEYS[:2])
        cells += _cell(stats[key]["context_recall"], 12)
        print(f"{key:<8}{n:>5}{cells}{e['hit']/n:>8.0%}{e['full']/n:>9.0%}")

    if args.judge: 
        print("\n===== 生成侧指标（LLM 裁判） =====")
        print(f"{'跳数':<8}{'答案正确性':>12}{'忠实度':>10}{'答案相关性':>12}")
        for key in ("single", "double", "triple", "quad", "ALL"):
            if not extra.get(key, {}).get("n"):
                continue
            row = _cell(stats[key]["answer_correctness"], 12) \
                + _cell(stats[key]["faithfulness"], 10) \
                + _cell(stats[key]["answer_relevance"], 12)
            print(f"{key:<8}{row}")
        na = stats["ALL"]["answer_correctness"]["n"] == 0
        if na:
            print("\n※ 答案正确性全列 N/A：golden_qa.jsonl 缺 expected_answer 标注。"
                  "补上该字段后本指标自动生效（不用改代码）。")

    print("\n指标口径：")
    print(f"  chunk召回@{args.topk} = 期望文档中被至少一个召回 chunk 覆盖的比例（文档级，P2 验收口径）")
    print(f"  chunk精确@{args.precision_at} = 注入深度前 {args.precision_at} 块（对齐 RAG_TOP_K_RERANK）中去重后来自期望文档的比例（越低=噪声越多）")
    print("  上下文召回    = 最终拼给 LLM 的 context 覆盖了多少期望文档（LLM 答对的上限）")
    print("  忠实度        = 答案被 context 支撑的比例（幻觉检测，低=在编）")
    print("  答案相关性    = 是否切题；答案正确性 = 对比 expected_answer")
    print(f"\nP2 验收看 ALL 行 chunk召回@{args.topk} ≥ 0.80。triple/quad 行偏低属正常；")
    print("  · triple/quad 全中率≈0 → FUSE_TOPK 过小 / 重排把同主题文档挤掉")
    print("  · 召回高但精确率低 → 噪声大，可下调 topk 或加强重排")
    print("  · 召回高但忠实度低 → 检索没问题，是生成在编，查 safety_qa 提示词约束")


def _cell(agg: dict, width: int) -> str:
    """指标单元格：无有效样本显示 N/A（不伪造 0.00 —— 会被误读成"检索烂"）。"""
    if not agg["n"]:
        return f"{'N/A':>{width}}"
    return f"{agg['sum'] / agg['n']:>{width}.2f}"


def _hop(qa: dict) -> str:
    """跳数：优先读 hop 字段，缺失时按 expected_docs 数量推断（1/2/3/4 → single/double/triple/quad）。"""
    if qa.get("hop"):
        return qa["hop"]
    n = len(qa.get("expected_docs", []))
    return {1: "single", 2: "double", 3: "triple", 4: "quad"}.get(n, "multi")


def _generate_answer(question: str, context: str) -> str:
    """复用线上 safety_qa 提示词生成答案，保证评测口径与 /api/chat 一致。"""
    from src.ai.llm_client import llm_client
    from src.ai.prompts import render

    system_prompt = render("safety_qa", retrieved_context=context, history="", question=question)
    return llm_client.chat([{"role": "system", "content": system_prompt}], temperature=0.3)


def _eval_one(qa: dict, topk: int, use_rewrite: bool = True,
              judge: bool = False, quiet: bool = False,
              precision_at: int = 4) -> dict:
    """单题评估：返回六指标 dict（不可用的指标为 None，绝不填 0 冒充）。"""
    question = qa["question"]
    expected = qa.get("expected_docs", [])
    context, sources = hybrid_retrieve(question, top_k=topk, use_rewrite=use_rewrite)

    out: dict = {
        "question": question,
        "hop": _hop(qa),
        "category": qa.get("category", ""),
        "expected_docs": expected,
        "got_chunks": [M.chunk_key(s) for s in sources],
        "chunk_recall_at_k": M.chunk_recall_at_k(expected, sources) if expected else None,
        "chunk_precision_at_k": M.chunk_precision_at_k(expected, sources[:precision_at]) if expected else None,
        "context_recall": M.context_recall(expected, context, sources) if expected else None,
        "answer_correctness": None,
        "faithfulness": None,
        "answer_relevance": None,
    }

    if judge:
        answer = ""
        try:
            answer = _generate_answer(question, context)
        except Exception as e:  # 生成失败不能让整轮崩
            out["gen_error"] = f"{type(e).__name__}: {e}"
        out["answer"] = answer
        if not answer.strip():
            # 关键：答案为空时裁判分数没有意义（忠实度 N/A + 相关性 0.00），
            # 必须把生成失败的真实原因暴露出来，否则会被误读成"模型答得差"。
            print(f"    ⚠ 答案生成失败，裁判分数无意义: {out.get('gen_error', '返回空字符串')}")
        f = M.faithfulness(context, answer)
        r = M.answer_relevance(question, answer)
        c = M.answer_correctness(question, answer, qa.get("expected_answer"))
        out["faithfulness"] = f.get("score")
        out["answer_relevance"] = r.get("score")
        out["answer_correctness"] = c.get("score")
        out["judge_detail"] = {"faithfulness": f, "answer_relevance": r, "answer_correctness": c}
        for name, d in (("faithfulness", f), ("answer_relevance", r)):
            if d.get("score") is None and d.get("error"):
                print(f"    ⚠ 裁判({name}) 失败: {d['error']}")

    _print_one(out, sources, quiet)
    return out


def _print_one(out: dict, sources: list[dict], quiet: bool) -> None:
    recall = out["chunk_recall_at_k"]
    if recall is None:  # 无期望标注：只展示召回内容，供人工补标
        print(f"[?] {out['question'][:40]}")
        for s in sources[:3]:
            print(f"    - {s['doc_type']} | {s['doc']} | "
                  f"{s.get('chapter') or s.get('article_no') or ''}")
        return
    if quiet and recall >= 1.0:
        return
    status = "✅" if recall >= 1.0 else ("◐" if recall > 0 else "❌")
    prec, ctx = out["chunk_precision_at_k"], out["context_recall"]
    print(f"{status} 召回{recall:.2f} 精确{prec:.2f} 上下文{ctx:.2f}  {out['question'][:28]}")
    missing = sorted(set(out["expected_docs"]) - {c.split('#')[0] for c in out["got_chunks"]})
    print(f"    期望({len(out['expected_docs'])}) 缺失: {missing or '无'}")
    if out.get("faithfulness") is not None or out.get("answer_relevance") is not None:
        def fmt(v):
            return "N/A" if v is None else f"{v:.2f}"
        print(f"    忠实度 {fmt(out['faithfulness'])} | 相关性 {fmt(out['answer_relevance'])} "
              f"| 正确性 {fmt(out['answer_correctness'])}")
        halluc = (out.get("judge_detail", {}).get("faithfulness", {}) or {}).get("hallucinated")
        if halluc:
            print(f"    ⚠ 疑似幻觉: {halluc[:2]}")


if __name__ == "__main__":
    main()
