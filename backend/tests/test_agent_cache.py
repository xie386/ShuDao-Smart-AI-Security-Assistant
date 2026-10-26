"""v2.0 大点 01：会话缓存 / 问题预处理 / 充分性判定 / 上下文预算的单测。

这些用例不打模型、不连 Redis、不连 MySQL：用假 Redis + 假 LLM，
只验证编排逻辑与护栏（拆分上限、预算裁剪、异常退化）。
真机链路的端到端验证见 Done/01 实现记录。
"""
import json

import pytest

from src.ai import query_preprocess, sufficiency
from src.ai.rag import format_context
from src.services import conv_rag_cache


# ---------------- 假 Redis ----------------

class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttl[key] = ttl
        return True

    def expire(self, key, ttl):
        if key not in self.data:
            return False
        self.ttl[key] = ttl
        return True

    def delete(self, key):
        self.data.pop(key, None)
        self.ttl.pop(key, None)
        return 1

    def ping(self):
        return True


class BrokenRedis:
    def __getattr__(self, _name):
        def _boom(*_a, **_kw):
            raise ConnectionError("redis down")

        return _boom


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(conv_rag_cache, "_client", lambda: fake)
    return fake


def _hit(text: str, doc: str = "docA", score: float = 0.5, chapter: str = "1") -> dict:
    return {
        "text": text,
        "rerank_score": score,
        "metadata": {"doc": doc, "doc_type": "法规/制度", "chapter": chapter,
                     "article_no": "", "source": f"{doc}.md"},
    }


# ---------------- 会话缓存 ----------------

def test_cache_roundtrip_and_summary(fake_redis):
    hits = [_hit("高处作业必须系安全带" * 10, score=0.9)]
    assert conv_rag_cache.put_blocks(7, hits, queries=["高处作业 安全带"]) is True

    obj = conv_rag_cache.get(7)
    assert obj is not None and len(obj["blocks"]) == 1
    assert obj["blocks"][0]["metadata"]["doc"] == "docA"
    assert "高处作业" in obj["blocks"][0]["text"]
    assert obj["queries"] == ["高处作业 安全带"]

    view = conv_rag_cache.summary(7)
    assert view and view[0]["doc"] == "docA" and len(view[0]["head"]) <= 200
    assert conv_rag_cache.available() is True
    assert conv_rag_cache.touch(7) is True
    assert conv_rag_cache.clear(7) is True
    assert conv_rag_cache.get(7) is None


def test_cache_dedup_and_cap(fake_redis):
    # 同一子块重复写入只留一份，且分数取高
    conv_rag_cache.put_blocks(1, [_hit("同一块" * 20, score=0.3)])
    conv_rag_cache.put_blocks(1, [_hit("同一块" * 20, score=0.8)])
    obj = conv_rag_cache.get(1)
    assert len(obj["blocks"]) == 1
    assert obj["blocks"][0]["rerank_score"] == 0.8

    # 超过上限只保留分数最高的 12 块
    conv_rag_cache.put_blocks(2, [_hit(f"块{i}" * 20, score=i / 100) for i in range(20)])
    obj2 = conv_rag_cache.get(2)
    assert len(obj2["blocks"]) == conv_rag_cache._MAX_BLOCKS
    scores = [b["rerank_score"] for b in obj2["blocks"]]
    assert scores == sorted(scores, reverse=True)


def test_cache_degrades_when_redis_down(monkeypatch):
    monkeypatch.setattr(conv_rag_cache, "_client", lambda: BrokenRedis())
    assert conv_rag_cache.available() is False
    assert conv_rag_cache.get(3) is None
    assert conv_rag_cache.put_blocks(3, [_hit("内容" * 30)]) is False
    assert conv_rag_cache.touch(3) is False
    assert conv_rag_cache.summary(3) == []


# ---------------- 问题预处理 ----------------

def _patch_llm(monkeypatch, payload: str):
    """让预处理/判定拿到指定的 LLM 输出。"""
    from src.ai import llm_client as llm_mod

    class _LC:
        def chat(self, _messages, temperature=None):
            return payload

    monkeypatch.setattr(llm_mod, "llm_client", _LC())


def test_preprocess_fast_path_skips_llm(monkeypatch):
    monkeypatch.setattr(
        query_preprocess, "_looks_declarative", query_preprocess._looks_declarative
    )
    out = query_preprocess.preprocess("高处作业安全要求")
    assert out["used_llm"] is False
    assert out["subqueries"] == ["高处作业安全要求"]
    assert out["split"] is False


def test_preprocess_question_form_uses_llm(monkeypatch):
    _patch_llm(
        monkeypatch,
        json.dumps(
            {"declarative": "高处作业的安全要求", "subqueries": ["高处作业的安全要求"],
             "split": False, "covered_dropped": [], "reason": "疑问句转陈述句"},
            ensure_ascii=False,
        ),
    )
    out = query_preprocess.preprocess("高处作业有哪些要求？")
    assert out["used_llm"] is True
    assert out["declarative"] == "高处作业的安全要求"
    assert out["subqueries"] == ["高处作业的安全要求"]
    assert out["split"] is False


def test_preprocess_split_capped_at_two(monkeypatch):
    _patch_llm(
        monkeypatch,
        json.dumps(
            {"declarative": "综合问题", "split": True,
             "subqueries": ["A实体", "B实体", "C实体"], "covered_dropped": [], "reason": "多实体"},
            ensure_ascii=False,
        ),
    )
    out = query_preprocess.preprocess("衣物怎么选、路线怎么排、天气怎么看？")
    assert out["split"] is True
    assert out["subqueries"] == ["A实体", "B实体"]


def test_preprocess_split_false_collapses_to_declarative(monkeypatch):
    _patch_llm(
        monkeypatch,
        json.dumps(
            {"declarative": "高处作业的防护要求与检查要点", "split": False,
             "subqueries": ["防护要求", "检查要点"], "covered_dropped": [], "reason": "同实体" },
            ensure_ascii=False,
        ),
    )
    out = query_preprocess.preprocess("高处作业的防护要求和检查要点分别是什么？")
    assert out["split"] is False
    assert out["subqueries"] == ["高处作业的防护要求与检查要点"]


def test_preprocess_bad_json_falls_back(monkeypatch):
    _patch_llm(monkeypatch, "我不知道怎么回答")
    out = query_preprocess.preprocess("这个该怎么弄？")
    assert out["used_llm"] is False
    assert out["declarative"] == "这个该怎么弄？"
    assert out["subqueries"]


def test_preprocess_carries_covered_dropped(monkeypatch):
    _patch_llm(
        monkeypatch,
        json.dumps(
            {"declarative": "补充检查要点", "split": False, "subqueries": ["补充检查要点"],
             "covered_dropped": ["安全带要求"], "reason": "上轮已覆盖" },
            ensure_ascii=False,
        ),
    )
    covered = [{"doc": "docA", "chapter": "1", "head": "安全带要求……"}]
    out = query_preprocess.preprocess("那还有哪些要注意的？", covered=covered)
    assert out["used_llm"] is True
    assert out["covered_dropped"] == ["安全带要求"]


# ---------------- 充分性判定 ----------------

def test_judge_sufficient(monkeypatch):
    _patch_llm(monkeypatch, json.dumps({"sufficient": True, "missing": [],
                                       "queries": ["不该用"], "reason": "够"}, ensure_ascii=False))
    out = sufficiency.judge("高处作业要求", blocks=[{"doc": "docA", "chapter": "1", "head": "…"}])
    assert out["sufficient"] is True
    assert out["queries"] == []


def test_judge_gap_queries_capped(monkeypatch):
    _patch_llm(
        monkeypatch,
        json.dumps(
            {"sufficient": False, "missing": ["检查周期"], "queries": ["检查周期", "罚款标准", "第三条"],
             "reason": "缺周期"},
            ensure_ascii=False,
        ),
    )
    out = sufficiency.judge("高处作业检查周期多久")
    assert out["sufficient"] is False
    assert len(out["queries"]) == 2


def test_judge_insufficient_without_queries_uses_question(monkeypatch):
    _patch_llm(monkeypatch, json.dumps({"sufficient": False, "missing": [], "queries": []},
                                       ensure_ascii=False))
    out = sufficiency.judge("防汛预案如何分级")
    assert out["queries"] == ["防汛预案如何分级"]


def test_judge_exception_is_conservative(monkeypatch):
    from src.ai import llm_client as llm_mod

    class _LC:
        def chat(self, *_a, **_kw):
            raise RuntimeError("llm down")

    monkeypatch.setattr(llm_mod, "llm_client", _LC())
    out = sufficiency.judge("高处作业要求", degraded=True)
    assert out["sufficient"] is False
    assert out["used_llm"] is False
    assert out["queries"] == ["高处作业要求"]


# ---------------- 上下文预算 ----------------

def test_format_context_respects_budget():
    parent = "前文" * 400 + "命中子块内容" * 50 + "后文" * 400
    blocks = [
        {"text": "命中子块内容" * 50,
         "metadata": {"doc": "docA", "chapter": "3", "parent_text": parent},
         "rerank_score": 0.9},
        {"text": "另一块" * 50,
         "metadata": {"doc": "docB", "article_no": "第5条", "parent_text": "另一块" * 500},
         "rerank_score": 0.8},
    ]
    ctx, src = format_context(blocks, budget_chars=2000)
    assert len(ctx) <= 2000 * 1.2
    assert "[来源: docA#3]" in ctx
    assert "[来源: docB#第5条]" in ctx
    assert len(src) == 2
    assert {"doc", "doc_type", "chapter", "article_no", "source"} <= set(src[0])


def test_format_context_dedupes_same_parent():
    same_parent = "父块内容" * 100
    blocks = [
        {"text": "子块1", "metadata": {"doc": "docA", "parent_text": same_parent}},
        {"text": "子块2", "metadata": {"doc": "docA", "parent_text": same_parent}},
    ]
    ctx, src = format_context(blocks, budget_chars=1000)
    assert ctx.count("[来源: docA]") == 1
    assert len(src) == 1


# ---------------- Agent 运行上下文（2026-10-23 真机 bug 回归） ----------------
# 背景：初版用 contextvars 传运行上下文，Starlette 迭代同步生成器时每次 next()
# 都在线程池的新 context 副本里跑 → `_CTX.reset(token)` 抛
# "<Token ...> was created in a different Context"，Agent 整体失败回落直连 LLM。
# 现改为随 LangChain config 传递；以下两条用例锁住该修复。

def test_run_ctx_comes_from_langchain_config():
    from langchain_core.runnables import RunnableLambda

    from src.ai import chat_agent

    run_ctx = {"conversation_id": 7, "calls": 0, "new_blocks": []}
    seen = RunnableLambda(lambda _x: chat_agent._current_run_ctx()).invoke(
        None, config={"configurable": {chat_agent._CTX_KEY: run_ctx}}
    )
    assert seen is run_ctx
    # 没传 config 时必须安全返回 None（工具据此跳过补检，而不是抛异常）
    assert chat_agent._current_run_ctx() is None


def test_chat_agent_does_not_use_contextvars():
    import contextvars

    from src.ai import chat_agent

    leaked = [
        name
        for name, value in vars(chat_agent).items()
        if isinstance(value, contextvars.ContextVar)
    ]
    assert leaked == [], f"chat_agent 又用回 contextvars 了：{leaked}"
