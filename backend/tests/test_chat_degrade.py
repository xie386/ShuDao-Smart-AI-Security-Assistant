"""v2.0 大点 03 裁定 5：检索失败降级（不再让整轮回答 500）。

真实触发过的事故：`langchain_chroma` 构造时用相对路径读 `.env`，盘掉线 → `OSError(22)`，
`_prepare()` 无兜底 → ASGI 500、SSE 断流、整轮回答丢失。
"""
import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.routers import chat as chat_router


def _fake_stream(messages, temperature=None):
    yield "本次未检索到资料，"


def _register(client: TestClient) -> dict:
    import uuid

    username = f"degrade_{uuid.uuid4().hex[:8]}"
    client.post("/api/auth/register", json={"username": username, "password": "pass123456"})
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    ).json()["access_token"]
    return {"headers": {"Authorization": f"Bearer {token}"}}


def test_safe_retrieve_swallows_errors():
    """检索抛任何异常都变成「空块 + 降级标记」，绝不向上冒。"""

    def _boom(_queries):
        raise OSError(22, "Invalid argument", ".env")

    original = chat_router.retrieve_queries
    try:
        chat_router.retrieve_queries = _boom
        blocks, degraded = chat_router._safe_retrieve(["高处作业"])
    finally:
        chat_router.retrieve_queries = original

    assert blocks == []
    assert degraded["kind"] == "retrieval"
    assert "OSError" in degraded["why"]


def test_retrieval_failure_degrades_instead_of_500(monkeypatch):
    monkeypatch.setattr(chat_router.settings, "AGENT_ENABLED", False)
    monkeypatch.setattr("src.ai.llm_client.llm_client.chat_stream", _fake_stream)

    def _boom(_queries):
        raise OSError(22, "Invalid argument", ".env")

    monkeypatch.setattr(chat_router, "retrieve_queries", _boom)

    with TestClient(app) as client:
        user = _register(client)
        resp = client.post(
            "/api/chat", json={"question": "高处作业有几米算高处？"}, headers=user["headers"]
        )
        assert resp.status_code == 200              # 关键：不再是 500
        text = resp.text
        assert '"kind": "retrieval"' in text        # meta 里带降级标记
        assert "本次未检索到资料" in text            # 回答照常流式输出
        assert "[DONE]" in text

        conv_id = client.get("/api/conversations", headers=user["headers"]).json()[0]["id"]
        msgs = client.get(f"/api/conversations/{conv_id}/messages",
                          headers=user["headers"]).json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["refs"] is None              # 降级不得伪造来源


def test_prepare_crash_also_degrades(monkeypatch):
    """编排整体崩（预处理/向量库初始化）也不能 500。"""
    monkeypatch.setattr(chat_router.settings, "AGENT_ENABLED", False)
    monkeypatch.setattr("src.ai.llm_client.llm_client.chat_stream", _fake_stream)

    def _boom(*_a, **_kw):
        raise RuntimeError("vectorstore init failed")

    monkeypatch.setattr(chat_router, "_prepare", _boom)

    with TestClient(app) as client:
        user = _register(client)
        resp = client.post(
            "/api/chat", json={"question": "脚手架验收要点"}, headers=user["headers"]
        )
        assert resp.status_code == 200
        assert '"kind": "retrieval"' in resp.text
        assert "本次未检索到资料" in resp.text


def test_degraded_prompt_tells_model_no_context(monkeypatch):
    """降级时喂给模型的资料段必须是「检索失败」说明，而不是空资料或假来源。"""
    seen = {}

    def _capture_stream(messages, temperature=None):
        seen["messages"] = messages
        yield "好的"

    monkeypatch.setattr(chat_router.settings, "AGENT_ENABLED", False)
    monkeypatch.setattr("src.ai.llm_client.llm_client.chat_stream", _capture_stream)
    monkeypatch.setattr(chat_router, "retrieve_queries",
                        lambda _q: (_ for _ in ()).throw(OSError(22, "Invalid argument", ".env")))

    with TestClient(app) as client:
        user = _register(client)
        client.post("/api/chat", json={"question": "动火作业怎么审批"}, headers=user["headers"])

    prompt = seen["messages"][0]["content"]
    assert "检索失败" in prompt
    assert "不含出处" in prompt


def test_trim_current_drops_last_user_line():
    history = "用户: 上一问\n助手: 上一答\n用户: 本轮问题"
    assert chat_router._trim_current(history, "本轮问题") == "用户: 上一问\n助手: 上一答"
    # 只有本轮一个问题 → 变成空（闲聊不带多余上下文）
    assert chat_router._trim_current("用户: 本轮问题", "本轮问题") == ""
