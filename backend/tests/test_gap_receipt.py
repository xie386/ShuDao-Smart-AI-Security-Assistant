"""04 v1.3：采集回执与「假上报」防护的单元测试。"""
from __future__ import annotations

import pytest

from src.ai import agent_scout as S
from src.ai.chat_agent import _make_tools


# ---------------------------------------------------------------- 回执文案
def test_notice_submitted_mentions_contrib_id():
    text = S._gap_notice("submitted", "塔吊高空事故救援", 42)
    assert "#42" in text and "审核通过后即可" in text


def test_notice_insufficient_says_no_record():
    text = S._gap_notice("insufficient", "塔吊高空事故救援", detail="模型判定材料不足以成文")
    assert "未生成待审记录" in text
    assert "不#" not in text          # 不该带贡献编号
    assert "模型判定材料不足以成文" in text


def test_notice_failed_says_no_record_and_reason():
    text = S._gap_notice("failed", "塔吊高空事故救援", detail="没有抓到可用正文")
    assert "未生成待审记录" in text and "没有抓到可用正文" in text


def test_notice_truncates_long_question():
    text = S._gap_notice("insufficient", "塔" * 100)
    assert "塔" * 31 not in text       # 只取前 30 字


def test_notify_asker_ignores_probe_conversations(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr("src.services.notification_service.NotificationService.push",
                        staticmethod(lambda *a, **k: called.__setitem__("n", 1)))
    S._notify_asker(-101, "探针问题", kind="failed")   # 负会话号 = 探针/离线
    S._notify_asker(0, "探针问题", kind="failed")
    assert called["n"] == 0


# ---------------------------------------------------------------- 工具文案不再死承诺
def test_gap_tool_text_is_conditional(monkeypatch):
    monkeypatch.setattr(S, "kick_off", lambda cid, q: True)
    from src.ai import chat_agent as ca

    ca._run_ctx = {"conversation_id": 7, "question": "塔吊高空救援", "gap_calls": 0}
    monkeypatch.setattr(ca, "_current_run_ctx", lambda: ca._run_ctx)
    tools = {t.name: t for t in _make_tools()}
    out = tools["report_knowledge_gap"].invoke({"reason": "缺少塔吊高空救援预案"})
    assert "已开始后台采集" in out
    assert "只有成功时才会生成" in out
    assert "会进入管理员审核" not in out          # 不得出现死承诺
    assert ca._run_ctx.get("gap_report", {}).get("accepted") is True


# ---------------------------------------------------------------- 僵尸任务回收
def test_reap_stale_runs_returns_rowcount(monkeypatch):
    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class _R:
                rowcount = 3
            return _R()

    class _Engine:
        def begin(self):
            return _Conn()

    monkeypatch.setattr("src.core.db.engine", _Engine())
    assert S.reap_stale_runs() == 3


def test_reap_stale_runs_swallows_errors(monkeypatch):
    class _Engine:
        def begin(self):
            raise RuntimeError("db down")

    monkeypatch.setattr("src.core.db.engine", _Engine())
    assert S.reap_stale_runs() == 0
