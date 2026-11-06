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


@pytest.fixture(autouse=True)
def _disable_sync_supplement(monkeypatch):
    """默认关闭「同步补料」（v2.0 大点 04 v1.4）。

    否则任何"检索为空/降级"的用例都会真去开 Playwright 联网采集 60s。
    需要验证门禁的用例自己把它打开并 monkeypatch `agent_scout.run_sync`。
    """
    from src.core.config import settings

    monkeypatch.setattr(settings, "SCOUT_SYNC_ENABLED", False, raising=False)


@pytest.fixture()
def db_session():
    """真实 MySQL 会话（本地集成测试用）；用完关闭，不自动回滚（调用方负责清理自己写入的行）。"""
    from src.core.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def _ensure_redis():
    """v2.0 大点 07：消息模块用例需要真实 Redis。

    复用项目自带的三态启动器（复用外部 → 拉起内置 → 降级）；结束时只关自己拉起的实例。
    Redis 不可用时用例里的断言会退化成"空列表"，不阻塞其他测试。
    """
    from src.core import redis_boot, redis_client

    state = {"state": "degraded"}
    try:
        state = redis_boot.ensure()
        redis_client.reset()          # URL 可能被运行期覆写（去 auth），必须重建连接池
        redis_client.get_redis()
    except Exception:  # noqa: BLE001
        pass
    try:
        yield state
    finally:
        try:
            redis_boot.stop()
        except Exception:  # noqa: BLE001
            pass
