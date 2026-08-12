#!/usr/bin/env python
"""AI 基建冒烟验证（D1）：嵌入模型 / 重排模型 / Chroma safety_kb / LLM 客户端配置。
 
用法（在 backend/ 目录下）：
    PYTHONPATH= C:\\python\\python.exe scripts/ai_verify.py

注：llm_client 实测需要 .env 中配置 API Key（D1 可能未配置，将打印"未配置"提示）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import settings  # noqa: E402


def verify_embedding() -> None:
    print("\n[1/3] 嵌入模型 bge-large-zh ...")
    from src.ai.embeddings import embedding_model

    t0 = time.time()
    vec = embedding_model.embed_query("高处作业有哪些安全要求")
    vecs = embedding_model.embed_documents(["安全带必须高挂低用", "脚手架搭设需经验收合格"])
    dim = len(vec)
    load_s = time.time() - t0
    print(f"  OK 维度={dim}, 查询1条+文档2条耗时={load_s:.1f}s")
    assert dim > 0 and len(vecs) == 2


def verify_reranker() -> None:
    print("\n[2/3] 重排模型 bge-reranker-base ...")
    from src.ai.reranker import reranker

    t0 = time.time()
    docs = [
        "高处作业人员必须系挂安全带，做到高挂低用，严禁低挂高用。",
        "食堂工作人员需持健康证上岗，注意食品卫生。",
        "登高作业前应检查作业平台、防护栏杆是否牢固可靠。",
    ]
    ranked = reranker.rerank("高处作业安全带要求", docs, top_k=2)
    print(f"  耗时={time.time()-t0:.1f}s, Top-2: {[(i, round(s, 3)) for i, s in ranked]}")
    # 相关文档应排在前面（index 0/2 相关，1 无关）
    assert ranked and ranked[0][0] in (0, 2)


def verify_chroma() -> None:
    print("\n[3/3] Chroma 集合 safety_kb ...")
    from src.ai.knowledge import get_collection
    from src.ai.embeddings import embedding_model

    col = get_collection()
    tag = "__smoke__"
    test_docs = [
        ("高处作业必须系挂安全带", {"doc": "冒烟测试", "doc_type": "操作规程", "tags": [tag]}),
        ("酒后严禁进入施工现场", {"doc": "冒烟测试", "doc_type": "法规/制度", "tags": [tag]}),
    ]
    ids = [f"smoke_{i}" for i in range(len(test_docs))]
    col.upsert(
        ids=ids,
        documents=[d[0] for d in test_docs],
        embeddings=embedding_model.embed_documents([d[0] for d in test_docs]),
        metadatas=[d[1] for d in test_docs],
    )
    q = embedding_model.embed_query("工地安全要求")
    # 注意：chromadb 1.5.9 不支持 list 字段 $in/$eq 过滤（抛 ValueError），
    # 多值字段(如 tags)必须用 $contains；标量字段可用 $eq/简写。
    hits = col.query(query_embeddings=[q], n_results=2, where={"tags": {"$contains": tag}})
    top = hits["documents"][0][0]
    print(f"  写入2条+检索命中: {top[:20]}...")
    assert top  # 有命中即可
    col.delete(ids=ids)  # 清理冒烟数据
    print("  冒烟数据已清理")


def verify_llm_config() -> None:
    print("\n[附] LLM 客户端配置 ...")
    if not settings.DEEPSEEK_API_KEY:
        print(f"  DEEPSEEK_API_KEY 未配置 → llm_client 实测待 DAY 配置后执行（主模型 {settings.DEEPSEEK_MODEL}）")
    else:
        print(f"  DEEPSEEK_API_KEY 已配置（{settings.DEEPSEEK_BASE_URL} / {settings.DEEPSEEK_MODEL}）")
    if not settings.AGNES_API_KEY:
        print(f"  AGNES_API_KEY 未配置（降级备选 {settings.AGNES_MODEL}）")


def main() -> None:
    verify_embedding()
    verify_reranker()
    verify_chroma()
    verify_llm_config()
    print("\n===== AI 基建冒烟验证通过 =====")


if __name__ == "__main__":
    main()
