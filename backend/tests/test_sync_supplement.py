"""v2.0 大点 04 v1.4：同步补料（先补再答）单测。"""
from __future__ import annotations

import pytest

from src.ai import agent_scout as S
from src.core.config import settings

MD_OK = "# 塔吊作业防护要求\n\n" + "\n".join(
    f"{i}. 作业人员应佩戴安全带并高挂低用，检查吊具与限位装置是否有效。" for i in range(1, 12)
) + "\n\n## 来源\n- https://www.mem.gov.cn/a.shtml\n"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(S, "_record_start", lambda *a, **k: 1)
    monkeypatch.setattr(S, "_record_finish", lambda *a, **k: None)
    monkeypatch.setattr(settings, "SCOUT_SYNC_MIN_CHARS", 300, raising=False)
    monkeypatch.setattr(settings, "KB_CONTRIB_MIN_CHARS", 1000, raising=False)
    monkeypatch.setattr(settings, "SCOUT_SYNC_TIMEOUT", 5.0, raising=False)
    yield


class _Page:
    def __init__(self, url):
        self.url = url
        self.title = "t"
        self.text = "x"


class _Col:
    def __init__(self, pages):
        self.pages = pages
        self.visited = [{"url": p.url, "ok": True} for p in pages]
        self.errors = []


def test_cache_key_is_stable_and_question_scoped():
    a = S._cache_key("塔吊作业要求")
    assert a == S._cache_key("塔吊作业要求")
    assert a == S._cache_key("  塔吊作业要求  ")      # 首尾空白不敏感
    assert a != S._cache_key("塔吊作业要求有哪些")     # 不同问题不串


def test_run_sync_uses_cache_without_collecting(monkeypatch):
    monkeypatch.setattr(S, "_cache_get", lambda q: {"md": MD_OK, "chars": len(MD_OK), "pages": 1,
                                                    "domains": ["mem.gov.cn"], "contrib_id": None, "note": ""})
    monkeypatch.setattr(S, "_collect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该采集")))

    res = S.run_sync(1, "塔吊作业要求")
    assert res["ok"] is True and res["cached"] is True and res["md"] == MD_OK


def test_run_sync_no_pages_degrades(monkeypatch):
    monkeypatch.setattr(S, "_cache_get", lambda q: None)
    monkeypatch.setattr(S, "_plan_queries", lambda q, m: ["q1"])
    monkeypatch.setattr(S, "_collect", lambda *a, **k: _Col([]))

    res = S.run_sync(1, "冷门问题")
    assert res["ok"] is False and "没有抓到" in res["note"] or "collected" in str(res)


def test_run_sync_model_insufficient(monkeypatch):
    monkeypatch.setattr(S, "_cache_get", lambda q: None)
    monkeypatch.setattr(S, "_plan_queries", lambda q, m: ["q1"])
    monkeypatch.setattr(S, "_collect", lambda *a, **k: _Col([_Page("https://a.gov.cn/x")]))
    monkeypatch.setattr(S, "_summarize", lambda q, pages: "INSUFFICIENT")

    res = S.run_sync(1, "冷门问题")
    assert res["ok"] is False and "不足以成文" in res["note"]


def _fake_db(monkeypatch):
    class _Agent:
        id = 1

    class _DB:
        def scalar(self, *a, **k):
            return _Agent()

        def close(self):
            pass

    monkeypatch.setattr("src.core.db.SessionLocal", lambda: _DB())


def test_run_sync_short_doc_not_submitted(monkeypatch):
    nl = chr(10)
    body = "".join(
        f"{i}. 作业人员进入现场前应检查安全帽、安全带与防护手套是否完好，发现破损立即更换，并做好记录。" + nl
        for i in range(1, 10)
    )
    short = "# 标题" + nl + nl + body + nl + "## 来源" + nl + "- https://a.gov.cn/x" + nl
    monkeypatch.setattr(S, "_cache_get", lambda q: None)
    monkeypatch.setattr(S, "_plan_queries", lambda q, m: ["q1"])
    monkeypatch.setattr(S, "_collect", lambda *a, **k: _Col([_Page("https://a.gov.cn/x")]))
    monkeypatch.setattr(S, "_summarize", lambda q, pages: short)
    monkeypatch.setattr(S, "_cache_put", lambda q, p: None)
    submitted = {"n": 0}
    monkeypatch.setattr("src.services.agent_contrib_service.submit_agent",
                        lambda *a, **k: submitted.__setitem__("n", 1))

    res = S.run_sync(1, "短文档问题")
    assert res["ok"] is True and res["contrib_id"] is None
    assert submitted["n"] == 0 and "1000" in res["note"]


def test_run_sync_long_doc_submits_and_caches(monkeypatch):
    nl = chr(10)
    extra = "".join(
        f"{i}. 作业前必须检查吊钩保险装置与钢丝绳断丝情况，发现异常立即停用并报告。" + nl
        for i in range(20, 60)
    )
    long_md = MD_OK.replace(nl + nl + "## 来源", nl + extra + "## 来源")
    _fake_db(monkeypatch)
    monkeypatch.setattr(S, "_cache_get", lambda q: None)
    monkeypatch.setattr(S, "_plan_queries", lambda q, m: ["q1"])
    monkeypatch.setattr(S, "_collect", lambda *a, **k: _Col([_Page("https://a.gov.cn/x")]))
    monkeypatch.setattr(S, "_summarize", lambda q, pages: long_md)

    cached = {}
    monkeypatch.setattr(S, "_cache_put", lambda q, p: cached.update(p))
    captured = {}

    class _Contrib:
        id = 77

    def _submit(db, **kw):
        captured.update(kw)
        return _Contrib()

    monkeypatch.setattr("src.services.agent_contrib_service.submit_agent", _submit)

    res = S.run_sync(1, "长文档问题")
    assert res["ok"] is True and res["contrib_id"] == 77
    assert captured["origin_urls"] == ["https://a.gov.cn/x"]
    assert cached["contrib_id"] == 77 and cached["domains"] == ["a.gov.cn"]


def test_sync_header_marks_unreviewed():
    from src.routers.chat import SYNC_SUPPLEMENT_HEADER

    assert "未审核" in SYNC_SUPPLEMENT_HEADER
    assert "公开网络来源" in SYNC_SUPPLEMENT_HEADER
