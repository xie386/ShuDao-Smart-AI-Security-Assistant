"""v2.0 大点 05：内置 Redis 启动器单测（全部假实现，不启真进程）。"""
from __future__ import annotations

import pytest

from src.core import redis_boot as rb
from src.core.config import settings

URL_WITH_AUTH = "redis://:123456@127.0.0.1:6380/0"
URL_NO_AUTH = "redis://127.0.0.1:6380/0"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(rb, "_proc", None, raising=False)
    monkeypatch.setattr(rb, "_pid", None, raising=False)
    monkeypatch.setattr(rb, "_state", {"state": "unknown", "message": "未初始化"}, raising=False)
    monkeypatch.setattr(settings, "REDIS_URL", URL_WITH_AUTH, raising=False)
    monkeypatch.setattr(settings, "REDIS_AUTOSTART", True, raising=False)
    yield


# ---------------------------------------------------------------- URL 变体
def test_variants_with_password_has_two_candidates():
    variants = rb._url_variants(URL_WITH_AUTH)
    kinds = [k for k, _ in variants]
    assert kinds == ["as_is", "no_auth"]
    no_auth = dict(variants)["no_auth"]
    assert "@" not in no_auth and "123456" not in no_auth
    assert no_auth.endswith(":6380/0")


def test_variants_without_password_has_one_candidate():
    assert [k for k, _ in rb._url_variants(URL_NO_AUTH)] == ["as_is"]


def test_port_parsing_defaults_to_6379():
    assert rb._port_of("redis://127.0.0.1:6380/0") == 6380
    assert rb._port_of("redis://127.0.0.1/0") == 6379


def test_mask_hides_password():
    masked = rb._mask(URL_WITH_AUTH)
    assert "123456" not in masked and "***" in masked and "6380" in masked


# ---------------------------------------------------------------- 复用外部
def test_ensure_reuses_external_with_auth(monkeypatch):
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (True, ""))
    called = {"spawn": 0}
    monkeypatch.setattr(rb, "_spawn", lambda url: called.__setitem__("spawn", 1) or {"ok": False})

    state = rb.ensure()
    assert state["state"] == "reuse" and state["kind"] == "as_is"
    assert settings.REDIS_URL == URL_WITH_AUTH          # 原样保留（带密码）
    assert called["spawn"] == 0                          # 不启第二份


def test_ensure_reuse_no_auth_overrides_runtime_url(monkeypatch):
    reset_calls = {"n": 0}
    from src.core import redis_client

    monkeypatch.setattr(redis_client, "reset", lambda: reset_calls.__setitem__("n", reset_calls["n"] + 1))
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (url == URL_NO_AUTH, "conn:ConnectionError"))

    state = rb.ensure()
    assert state["state"] == "reuse" and state["kind"] == "no_auth"
    assert settings.REDIS_URL == URL_NO_AUTH             # 运行时覆写为无 auth
    assert reset_calls["n"] == 1                         # 连接池已清


def test_ensure_logs_auth_error_then_falls_through(monkeypatch, caplog):
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (False, "auth" if url == URL_WITH_AUTH else "conn"))
    monkeypatch.setattr(rb, "_spawn", lambda url: {"ok": False, "message": "端口被占用"})

    with caplog.at_level("WARNING"):
        state = rb.ensure()
    assert state["state"] == "degraded"
    assert any("AUTH" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------- 拉起内置
def test_ensure_autostart_off_degrades(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_AUTOSTART", False, raising=False)
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (False, "conn"))
    called = {"spawn": 0}
    monkeypatch.setattr(rb, "_spawn", lambda url: called.__setitem__("spawn", 1) or {"ok": False})

    state = rb.ensure()
    assert state["state"] == "degraded" and called["spawn"] == 0
    assert "REDIS_AUTOSTART" in state["message"]


def test_ensure_missing_binaries_reports_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REDIS_BIN_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (False, "conn"))

    state = rb.ensure()
    assert state["state"] == "degraded"
    assert "缺失" in state["message"] and "get_redis.py" in state["message"]


def test_ensure_spawn_success_marks_internal(monkeypatch):
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (False, "conn"))
    monkeypatch.setattr(
        rb, "_spawn", lambda url: {"ok": True, "pid": 4321, "port": 6380, "message": "内置 Redis 已启动 pid=4321"}
    )

    state = rb.ensure()
    assert state["state"] == "internal" and state["pid"] == 4321


def test_ensure_spawn_timeout_degrades(monkeypatch):
    monkeypatch.setattr(rb, "_probe", lambda url, timeout=0.6: (False, "conn"))
    monkeypatch.setattr(rb, "_spawn", lambda url: {"ok": False, "message": "内置 Redis 5.0s 内未就绪（端口 6380）"})

    state = rb.ensure()
    assert state["state"] == "degraded" and "未就绪" in state["message"]


# ---------------------------------------------------------------- 生命周期
class _FakeProc:
    def __init__(self, alive: bool = True, code: int | None = None):
        self._alive = alive
        self.returncode = code
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else self.returncode

    def terminate(self):
        self.terminated = True
        self._alive = False
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


def test_stop_terminates_own_process(monkeypatch):
    proc = _FakeProc()
    monkeypatch.setattr(rb, "_proc", proc, raising=False)
    monkeypatch.setattr(rb, "_pid", 999, raising=False)

    rb.stop()
    assert proc.terminated is True
    assert rb._proc is None and rb._pid is None


def test_stop_noop_when_nothing_spawned():
    rb.stop()  # 不抛异常
    assert rb._proc is None


def test_stop_skips_already_dead(monkeypatch):
    proc = _FakeProc(alive=False, code=1)
    monkeypatch.setattr(rb, "_proc", proc, raising=False)
    rb.stop()
    assert proc.terminated is False          # 已退出就不动它


# ---------------------------------------------------------------- 惰性客户端
def test_lazy_redis_delegates_and_resets(monkeypatch):
    from src.core import redis_client as rc

    calls = {"n": 0}

    class _Fake:
        def ping(self):
            calls["n"] += 1
            return True

    def _factory():
        return _Fake()

    _factory.cache_clear = lambda: calls.__setitem__("cleared", True)  # reset() 会用到
    monkeypatch.setattr(rc, "get_redis", _factory)
    assert rc.redis_client.ping() is True
    assert calls["n"] == 1

    rc.reset()
    assert calls.get("cleared") is True


def test_status_contains_bin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REDIS_BIN_DIR", str(tmp_path), raising=False)
    st = rb.status()
    assert st["bin_dir"] == str(tmp_path)


# ---------------------------------------------------------------- 装载失败：提示 + 重试
def test_spawn_retries_once_on_loader_failure(monkeypatch):
    calls = {"n": 0}

    def _fake_once(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": False, "retry": True, "message": "内置 Redis 启动即退出（code=-1073741515，缺 DLL）"}
        return {"ok": True, "pid": 1, "port": 6380, "message": "内置 Redis 已启动 pid=1 port=6380"}

    monkeypatch.setattr(rb, "_spawn_once", _fake_once)
    monkeypatch.setattr(rb.time, "sleep", lambda _s: None)
    res = rb._spawn(URL_WITH_AUTH)
    assert res["ok"] is True and calls["n"] == 2


def test_spawn_does_not_retry_plain_failure(monkeypatch):
    calls = {"n": 0}

    def _fake_once(url):
        calls["n"] += 1
        return {"ok": False, "message": "端口被占用"}

    monkeypatch.setattr(rb, "_spawn_once", _fake_once)
    res = rb._spawn(URL_WITH_AUTH)
    assert res["ok"] is False and calls["n"] == 1


def test_loader_exit_codes_have_hints():
    # 0xC0000135 缺 DLL / 0xC000007B 位数不符 / 0xC0000142 DLL 初始化失败
    for code in (-1073741515, -1073741701, -1073741502):
        assert code in rb._EXIT_HINTS
