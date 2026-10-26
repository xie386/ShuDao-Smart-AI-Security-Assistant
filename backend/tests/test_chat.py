"""AI 问答测试（A01–A04）。

- LLM 流式用 monkeypatch 模拟（不依赖网络/费用）；
- 覆盖：SSE 格式、会话自动创建、消息落库（user+assistant）、多轮上下文、
  会话列表/消息/删除、归属校验、快捷提问。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from src.ai.llm_client import llm_client
from src.main import app
from src.routers import chat as chat_router


@pytest.fixture(autouse=True)
def _fast_chat(monkeypatch):
    """本文件只验证 SSE 格式与落库：不连 Chroma、不走真模型。

    v2.0 大点 03 起 Agent 会先做意图判定、再走 Agent 生成；单测里两条外部依赖都要掐掉，
    否则用例会随网络/向量库状态飘（曾出现「Agent 真答了 → 断言假流文本失败」）。
    """
    monkeypatch.setattr(chat_router.settings, "AGENT_ENABLED", False)
    monkeypatch.setattr(chat_router, "retrieve_queries", lambda *_a, **_kw: [])


def _register(client: TestClient) -> dict:
    username = f"chat_{uuid.uuid4().hex[:8]}"
    client.post("/api/auth/register", json={"username": username, "password": "pass123456"})
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    ).json()["access_token"]
    return {"headers": {"Authorization": f"Bearer {token}"}}


def _fake_stream(messages, temperature=None):
    yield "高处作业"
    yield "必须系挂安全带，做到高挂低用。"


def test_chat_sse_flow(monkeypatch):
    """A01/A03：SSE 流式 + 会话自动创建 + 消息落库。"""
    monkeypatch.setattr(llm_client, "chat_stream", _fake_stream)
    with TestClient(app) as client:
        user = _register(client)
        resp = client.post(
            "/api/chat", json={"question": "高处作业有哪些要求？"}, headers=user["headers"]
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        text = resp.text
        assert "高处作业" in text and "高挂低用" in text  # 流式 delta 到达
        assert "[DONE]" in text

        # 会话自动创建 + 消息 user/assistant 已落库
        convs = client.get("/api/conversations", headers=user["headers"]).json()
        assert len(convs) == 1
        conv_id = convs[0]["id"]
        msgs = client.get(
            f"/api/conversations/{conv_id}/messages", headers=user["headers"]
        ).json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["content"] == "高处作业必须系挂安全带，做到高挂低用。"


def test_chat_multi_turn_history(monkeypatch):
    """多轮：同一会话第二次提问，历史包含第一轮。"""
    monkeypatch.setattr(llm_client, "chat_stream", _fake_stream)
    with TestClient(app) as client:
        user = _register(client)
        r1 = client.post("/api/chat", json={"question": "第一问"}, headers=user["headers"])
        conv_id = None  # 从响应流里拿不到 conv_id（SSE），从列表取
        convs = client.get("/api/conversations", headers=user["headers"]).json()
        conv_id = convs[0]["id"]
        client.post(
            "/api/chat",
            json={"conversation_id": conv_id, "question": "第二问"},
            headers=user["headers"],
        )
        msgs = client.get(
            f"/api/conversations/{conv_id}/messages", headers=user["headers"]
        ).json()
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert msgs[0]["content"] == "第一问"
        assert msgs[2]["content"] == "第二问"


def test_conversation_ownership():
    """他人会话：消息查询/删除应 404。"""
    with TestClient(app) as client:
        u1 = _register(client)
        u2 = _register(client)
        # u1 无会话时 u2 查 u1 的会话 id=999
        resp = client.get("/api/conversations/999/messages", headers=u2["headers"])
        assert resp.status_code == 404
        resp = client.delete("/api/conversations/999", headers=u2["headers"])
        assert resp.status_code == 404


def test_quick_questions():
    """A04 快捷提问。"""
    with TestClient(app) as client:
        user = _register(client)
        resp = client.get("/api/chat/quick-questions", headers=user["headers"])
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 3
        assert all("label" in i and "question" in i for i in items)


def test_chat_requires_auth():
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"question": "hi"})
        assert resp.status_code == 401
