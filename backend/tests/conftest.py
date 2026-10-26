"""测试公用夹具。

v2.0 大点 03 起，`/api/chat` 每轮会先做一次真实 LLM 意图判定。单测不能因此打网络/付费，
所以默认把 `intent.detect` 换成「总是 knowledge」的假实现；需要验证门禁的用例在自己的
monkeypatch 里覆盖它（autouse 夹具先执行，用例内覆盖生效）。
"""
import pytest

from src.ai import intent as intent_mod


@pytest.fixture(autouse=True)
def _stub_intent(monkeypatch):
    """默认不做真实意图判定（等价于「判定器认为这是知识问答」）。"""
    monkeypatch.setattr(
        intent_mod,
        "detect",
        lambda question, hint="": intent_mod.IntentResult(
            label="knowledge", reason="单测默认（未调用模型）", model="test"
        ),
    )


@pytest.fixture()
def db_session():
    """真实 MySQL 会话（本地集成测试用）；用完关闭，不自动回滚（调用方负责清理自己写入的行）。"""
    from src.core.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
