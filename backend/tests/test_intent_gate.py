"""v2.0 大点 03：意图识别（回答前门禁）单测。

覆盖裁定 1–4 与裁定 6（审计口径）：
- 解析容错（JSON / 裸词 / 围栏 / 垃圾输出）
- 失败与不可解析 → 保守回退 knowledge + degraded
- 开关关闭 → 不调模型
- 历史注入（上一轮问题 + 回答开头）与长度上限
- 闲聊轮不写 RAG 缓存、refs.intent 落库、meta.mode=chat
- 审计 build_rounds 对「只有 intent 没有 preprocessed」的轮次照常展示
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.ai import intent as intent_mod
from src.main import app
from src.routers import chat as chat_router
from src.services import conv_rag_cache
from src.services import preprocess_audit

# autouse 夹具把 detect 换成了假实现；这里把真实函数抓在手里做单元测试
_REAL_DETECT = intent_mod.detect


# ---------------- 假件 ----------------

class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        return True

    def expire(self, key, ttl):
        return key in self.data

    def delete(self, key):
        self.data.pop(key, None)
        return 1

    def ping(self):
        return True


class FakeModel:
    """假模型：返回固定文本；可让它抛异常。"""

    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self.content = content
        self.exc = exc

    def invoke(self, _messages):
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=self.content)


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(conv_rag_cache, "_client", lambda: fake)
    return fake


def _fake_stream(messages, temperature=None):
    yield "好的，"


def _register(client: TestClient) -> dict:
    import uuid

    username = f"intent_{uuid.uuid4().hex[:8]}"
    client.post("/api/auth/register", json={"username": username, "password": "pass123456"})
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    ).json()["access_token"]
    return {"headers": {"Authorization": f"Bearer {token}"}}


# ---------------- 解析 ----------------

@pytest.mark.parametrize(
    "raw, want",
    [
        ('{"intent":"chat","reason":"问候"}', ("chat", "问候")),
        ('{"label":"knowledge","reason":"业务问题"}', ("knowledge", "业务问题")),
        ('```json\n{"intent":"chat","reason":"元问题"}\n```', ("chat", "元问题")),
        ("chat", ("chat", "")),
        ("  KNOWLEDGE  ", ("knowledge", "")),
    ],
)
def test_parse_variants(raw, want):
    assert intent_mod._parse(raw) == want


@pytest.mark.parametrize("raw", ["", "我觉得这是闲聊吧", '{"foo": 1}', "null"])
def test_parse_rejects_garbage(raw):
    assert intent_mod._parse(raw) is None


# ---------------- detect ----------------

def test_detect_chat(monkeypatch):
    monkeypatch.setattr(intent_mod, "_model", lambda: FakeModel('{"intent":"chat","reason":"询问能力"}'))
    out = _REAL_DETECT("不用检索,你现在有自我维护和丰富知识库的能力吗")
    assert out.label == "chat" and out.is_chat
    assert out.reason == "询问能力"
    assert out.ms >= 0 and out.model
    assert out.degraded is None
    assert out.to_dict()["label"] == "chat"


def test_detect_model_error_falls_back_to_knowledge(monkeypatch):
    monkeypatch.setattr(intent_mod, "_model", lambda: FakeModel(exc=TimeoutError("timeout")))
    out = _REAL_DETECT("高处作业几米算高处")
    assert out.label == "knowledge" and not out.is_chat
    assert out.degraded and out.degraded["kind"] == "intent"
    assert "TimeoutError" in out.degraded["why"]


def test_detect_unparsable_falls_back_to_knowledge(monkeypatch):
    monkeypatch.setattr(intent_mod, "_model", lambda: FakeModel("这个我说不准"))
    out = _REAL_DETECT("脚手架验收要点")
    assert out.label == "knowledge"
    assert out.degraded and out.degraded["why"] == "unparsable"


def test_detect_disabled_skips_model(monkeypatch):
    monkeypatch.setattr(intent_mod.settings, "CHAT_INTENT_ENABLED", False)
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("开关关闭时不应调用模型")

    monkeypatch.setattr(intent_mod, "_model", _boom)
    out = _REAL_DETECT("你是谁")
    assert out.label == "knowledge" and called["n"] == 0
    assert "关闭" in out.reason


def test_detect_injects_previous_round_and_caps_length(monkeypatch):
    captured = {}

    def _fake_render(name, **kwargs):
        captured["name"] = name
        captured.update(kwargs)
        return "PROMPT"

    monkeypatch.setattr("src.ai.prompts.render", _fake_render)
    monkeypatch.setattr(intent_mod, "_model", lambda: FakeModel('{"intent":"knowledge","reason":"指代追问"}'))
    monkeypatch.setattr(intent_mod.settings, "CHAT_INTENT_MAX_HISTORY_CHARS", 40)
    hint = "上一轮问题：" + "非常长的问题" * 20 + "\n上一轮回答开头：答案"
    out = _REAL_DETECT("第一问", hint=hint)

    assert captured["name"] == "chat_intent"
    assert len(captured["history"]) <= 40          # 上限生效
    assert captured["question"] == "第一问"         # 当前问题原样传入
    assert out.label == "knowledge"


def test_detect_empty_question():
    out = _REAL_DETECT("   ")
    assert out.label == "knowledge" and out.degraded["why"] == "empty"


def test_model_is_dedicated_with_timeout(monkeypatch):
    """判定客户端必须是独立实例：调用级超时 + 不重试（否则 8s 会被重试放大）。"""
    import langchain_openai

    intent_mod._client = None
    m = intent_mod._model()
    assert isinstance(m, langchain_openai.ChatOpenAI)
    assert float(m.request_timeout) == float(intent_mod.settings.CHAT_INTENT_TIMEOUT)
    assert int(m.max_retries) == 0
    intent_mod._client = None


# ---------------- last_round_hint（裁定 2） ----------------

class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeSession:
    def __init__(self, msgs):
        self._msgs = msgs

    def scalars(self, _stmt):
        return _ScalarResult(self._msgs)


def test_last_round_hint_uses_previous_round_only():
    from src.services.chat_service import ChatService

    msgs = [  # 按 id 倒序（与 SQL 一致）：最近的是上一条 assistant
        SimpleNamespace(role="assistant", content="高处作业指坠落高度基准面2m以上…" + "细节" * 200),
        SimpleNamespace(role="user", content="高处作业有哪些要求？"),
    ]
    hint = ChatService.last_round_hint(_FakeSession(msgs), conversation_id=1)
    assert "上一轮问题：高处作业有哪些要求？" in hint
    assert "上一轮回答开头：" in hint
    assert len(hint) < 400          # 回答只取前 200 字
    assert "第一问" not in hint


def test_last_round_hint_empty_conversation():
    from src.services.chat_service import ChatService

    assert ChatService.last_round_hint(_FakeSession([]), conversation_id=1) == ""


# ---------------- 审计（裁定 6） ----------------

def _msg(mid, role, content, refs=None, rating=None):
    return SimpleNamespace(
        id=mid, role=role, content=content, refs=refs, rating=rating, created_at=None
    )


def test_parse_intent_normalizes():
    refs = json.dumps({"intent": {"label": "weird", "reason": "x", "ms": "12",
                                  "model": "m", "degraded": "oops"}})
    out = preprocess_audit.parse_intent(refs)
    assert out["label"] == "knowledge"          # 非法值归一到保守档
    assert out["ms"] == 12
    assert out["degraded"] is None              # 非 dict 的降级信息丢弃
    assert preprocess_audit.parse_intent(None) is None


def test_build_rounds_shows_chat_round_without_preprocess():
    msgs = [
        _msg(1, "user", "你是谁", refs=json.dumps({"intent": {"label": "chat", "reason": "元问题", "ms": 900}})),
        _msg(2, "assistant", "我是蜀道安全助手…", refs=None),
    ]
    rounds = preprocess_audit.build_rounds(msgs)
    assert len(rounds) == 1
    r = rounds[0]
    assert r["preprocessed"] is None
    assert r["intent"]["label"] == "chat"
    assert r["intent"]["ms"] == 900


# ---------------- 端到端：闲聊门禁（裁定 3） ----------------

def test_chat_endpoint_chat_mode_skips_retrieval(monkeypatch, fake_redis):
    monkeypatch.setattr(chat_router.intent, "detect",
                        lambda question, hint="": intent_mod.IntentResult(
                            label="chat", reason="询问能力", ms=800, model="test-model"))
    monkeypatch.setattr(chat_router.settings, "AGENT_ENABLED", False)
    monkeypatch.setattr("src.ai.llm_client.llm_client.chat_stream", _fake_stream)
    monkeypatch.setattr(chat_router, "retrieve_queries",
                        lambda *_a, **_kw: pytest.fail("闲聊不应触发检索"))

    with TestClient(app) as client:
        user = _register(client)
        resp = client.post("/api/chat",
                           json={"question": "你现在有自我维护知识库的能力吗"},
                           headers=user["headers"])
        assert resp.status_code == 200
        text = resp.text
        assert '"mode": "chat"' in text
        assert '"intent"' in text and '"label": "chat"' in text
        assert '"sources": 0' in text
        assert "好的，" in text and "[DONE]" in text

        # 落库：user.refs = {intent}，assistant 无 refs（无来源）
        conv_id = client.get("/api/conversations", headers=user["headers"]).json()[0]["id"]
        msgs = client.get(f"/api/conversations/{conv_id}/messages",
                          headers=user["headers"]).json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        audit = client.get(f"/api/conversations/{conv_id}/preprocess-audit",
                           headers=user["headers"]).json()
        assert audit["rounds_with_intent"] == 1 and audit["chat_rounds"] == 1
        assert audit["rounds"][0]["preprocessed"] is None
        assert audit["rounds"][0]["intent"]["label"] == "chat"

    # 裁定 3：闲聊不写会话 RAG 缓存
    assert fake_redis.data == {}
