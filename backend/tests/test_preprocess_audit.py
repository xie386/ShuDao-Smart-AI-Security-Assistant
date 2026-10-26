"""A08 预处理审计：服务层配对逻辑 + HTTP 端点（含归属隔离）。

不打模型、不跑检索：数据直接种库，验证的是「原问题 → 预处理结果」的配对与鉴权。
"""
import json
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.core.db import SessionLocal
from src.main import app
from src.models.conversation import ChatMessage, Conversation
from src.models.user import User
from src.services.preprocess_audit import build_rounds, parse_preprocessed

PRE = {
    "declarative": "安全带正确使用方法的规范要求",
    "subqueries": ["安全带正确使用方法的规范要求"],
    "split": False,
    "covered_dropped": [],
    "reason": "疑问句改陈述句",
    "used_llm": True,
}


def _msg(mid, role, content, refs=None):
    """构造轻量消息替身（build_rounds 只用到 id/role/content/refs/rating）。"""
    return SimpleNamespace(
        id=mid, role=role, content=content, refs=refs, rating=None, created_at=None
    )


def _register(client: TestClient) -> tuple[str, dict]:
    username = f"audit_{uuid.uuid4().hex[:8]}"
    client.post("/api/auth/register", json={"username": username, "password": "pass123456"})
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    ).json()["access_token"]
    return username, {"Authorization": f"Bearer {token}"}


# ---------------- 服务层 ----------------

def test_parse_preprocessed_handles_missing_and_garbage():
    assert parse_preprocessed(None) is None
    assert parse_preprocessed("not-json") is None
    assert parse_preprocessed(json.dumps({"other": 1})) is None
    out = parse_preprocessed(json.dumps({"preprocessed": PRE}, ensure_ascii=False))
    assert out["declarative"] == PRE["declarative"]
    assert out["split"] is False and out["used_llm"] is True


def test_build_rounds_pairs_question_with_answer():
    msgs = [
        _msg(1, "user", "问一", json.dumps({"preprocessed": PRE}, ensure_ascii=False)),
        _msg(2, "assistant", "答一", json.dumps([{"doc": "d1", "chapter": "c1"}], ensure_ascii=False)),
        _msg(3, "user", "问二", None),
        _msg(4, "assistant", "答二", json.dumps([], ensure_ascii=False)),
    ]
    rounds = build_rounds(msgs)
    assert len(rounds) == 2
    assert rounds[0]["question"] == "问一"
    assert rounds[0]["assistant_message_id"] == 2
    assert rounds[0]["sources"] == 1
    assert rounds[0]["preprocessed"]["declarative"] == PRE["declarative"]
    # 第二轮 user 没带 refs（01 之前的数据）→ preprocessed 为 None，但轮次仍存在
    assert rounds[1]["assistant_message_id"] == 4
    assert rounds[1]["preprocessed"] is None


def test_build_rounds_keeps_unanswered_question():
    rounds = build_rounds([_msg(9, "user", "只问了没答", None)])
    assert len(rounds) == 1
    assert rounds[0]["assistant_message_id"] is None
    assert rounds[0]["sources"] == 0


# ---------------- HTTP 端点 ----------------

def test_audit_endpoint_ok_and_isolation():
    with TestClient(app) as client:
        username, headers = _register(client)
        _, other_headers = _register(client)

        db = SessionLocal()
        try:
            me = db.scalars(select(User).where(User.username == username)).first()
            conv = Conversation(user_id=me.id, title="审计测试会话")
            db.add(conv)
            db.commit()
            db.refresh(conv)
            conv_id = conv.id
            db.add(
                ChatMessage(
                    conversation_id=conv_id,
                    role="user",
                    content="安全带怎么用？",
                    refs=json.dumps({"preprocessed": PRE}, ensure_ascii=False),
                )
            )
            db.add(
                ChatMessage(
                    conversation_id=conv_id,
                    role="assistant",
                    content="这样用……",
                    refs=json.dumps([{"doc": "docA", "chapter": "1"}], ensure_ascii=False),
                )
            )
            db.commit()
        finally:
            db.close()

        resp = client.get(f"/api/conversations/{conv_id}/preprocess-audit", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == conv_id
        assert body["total_rounds"] == 1
        assert body["rounds_with_preprocess"] == 1
        round0 = body["rounds"][0]
        assert round0["question"] == "安全带怎么用？"
        assert round0["preprocessed"]["declarative"] == PRE["declarative"]
        assert round0["sources"] == 1

        # 他人会话 → 404（与 /messages 一致的归属口径）
        assert (
            client.get(f"/api/conversations/{conv_id}/preprocess-audit", headers=other_headers)
            .status_code
            == 404
        )
        # 不存在的会话 → 404
        assert (
            client.get("/api/conversations/99999999/preprocess-audit", headers=headers)
            .status_code
            == 404
        )
        # 未登录 → 401/403
        assert client.get(f"/api/conversations/{conv_id}/preprocess-audit").status_code in (401, 403)
