"""v2.0 大点 07 单测：Redis 消息（HASH+ZSET）、闭环动作、记录表与可见性。

- 消息类用例走真实 Redis（conftest 会话级拉起内置实例），uid 用 9xxxxx 段、用完即清；
- 记录/验收类用例用真实 MySQL，创建临时隐患并在结束时删干净；
- 验收流程里的 109 个 admin 推送用 monkeypatch 截获，避免污染真实用户的消息箱。
"""
from __future__ import annotations

import json
import time

import pytest

from src.services import notification_service as N
from src.services import record_service

UID = 990001


@pytest.fixture()
def clean_uid():
    N.clear(UID)
    yield UID
    N.clear(UID)


# --------------------------------------------------------------------------- #
# 1) 消息：顺序 / TTL / 过期清理 / 已读 / 删除 / 上限
# --------------------------------------------------------------------------- #
def test_push_list_order_newest_first(clean_uid):
    for i in range(3):
        N.push(clean_uid, f"m{i}", hazard_id=1, msg_type="report")
        time.sleep(0.01)
    msgs = N.list_for(clean_uid)
    assert [m["message"] for m in msgs] == ["m2", "m1", "m0"]
    assert N.total_count(clean_uid) == 3


def test_ttl_sets_expire_at(clean_uid):
    N.push(clean_uid, "长期", msg_type="report")
    N.push(clean_uid, "短期", msg_type="final", ttl=86400)
    by_msg = {m["message"]: m for m in N.list_for(clean_uid)}
    assert by_msg["长期"]["expire_at"] is None
    assert by_msg["短期"]["expire_at"] and by_msg["短期"]["expire_at"] > int(time.time())


def test_expired_messages_are_swept(clean_uid):
    mid = N.push(clean_uid, "过期项", msg_type="final", ttl=86400)
    hkey, zkey = N._key(clean_uid), N._zkey(clean_uid)
    raw = json.loads(N.redis_client.hget(hkey, mid))
    raw["expire_at"] = int(time.time()) - 5
    N.redis_client.hset(hkey, mid, json.dumps(raw, ensure_ascii=False))
    assert N.list_for(clean_uid) == []
    assert N.redis_client.zcard(zkey) == 0


def test_unread_and_mark_read(clean_uid):
    for i in range(3):
        N.push(clean_uid, f"m{i}", msg_type="report")
    assert N.unread_count(clean_uid) == 3
    ids = [m["id"] for m in N.list_for(clean_uid)]
    assert N.mark_read(clean_uid, ids=ids[:2]) == 1
    assert N.total_count(clean_uid) == 3          # 已读不影响总数
    assert N.mark_read(clean_uid, all_=True) == 0


def test_count_shape(clean_uid):
    N.push(clean_uid, "唯一一条", msg_type="assign", hazard_id=5)
    c = N.count(clean_uid)
    assert c["unread"] == 1 and c["total"] == 1
    assert c["latest"]["message"] == "唯一一条" and c["latest"]["hazard_id"] == 5


def test_drop_by_hazard_and_types(clean_uid):
    N.push(clean_uid, "上报", hazard_id=7, msg_type="report")
    N.push(clean_uid, "整改", hazard_id=7, msg_type="rectify")
    N.push(clean_uid, "别的隐患", hazard_id=8, msg_type="report")
    assert N.drop(clean_uid, hazard_id=7, types=["report", "rectify"]) == 2
    left = [m["message"] for m in N.list_for(clean_uid)]
    assert left == ["别的隐患"]


def test_drop_rejected_only(clean_uid):
    N.push(clean_uid, "驳回1", hazard_id=9, msg_type="accept", status="rejected")
    N.push(clean_uid, "驳回2", hazard_id=9, msg_type="accept", status="rejected")
    N.push(clean_uid, "通过", hazard_id=9, msg_type="accept", status="passed")
    assert N.drop(clean_uid, hazard_id=9, types=["accept"], status="rejected") == 2
    assert [m["message"] for m in N.list_for(clean_uid)] == ["通过"]


def test_drop_requires_filter(clean_uid):
    N.push(clean_uid, "x", msg_type="general")
    assert N.drop(clean_uid) == 0                  # 不带条件不误删
    assert N.total_count(clean_uid) == 1


def test_push_none_user_is_dropped(clean_uid):
    assert N.push(None, "孤儿") is None
    assert N.redis_client.exists("shudao:notify:None") == 0


def test_max_per_user_trims_oldest(monkeypatch, clean_uid):
    from src.core.config import settings

    monkeypatch.setattr(settings, "NOTIFY_MAX_PER_USER", 5, raising=False)
    for i in range(8):
        N.push(clean_uid, f"m{i}", msg_type="report")
        time.sleep(0.005)
    msgs = N.list_for(clean_uid, limit=50)
    assert len(msgs) == 5
    assert msgs[0]["message"] == "m7" and msgs[-1]["message"] == "m3"


def test_redis_down_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(N.redis_client, "zrange", boom, raising=False)
    assert N.list_for(999999) == []


# --------------------------------------------------------------------------- #
# 2) 记录表：幂等 / 快照 / 可见性
# --------------------------------------------------------------------------- #
def _make_hazard(db, *, status="待验收", reporter_id=1, assignee_id=2):
    from src.models.hazard import Hazard, HazardTimeline

    h = Hazard(
        title="单测闭环隐患", description="用于 07 记录用例", level="一般",
        status=status, reporter_id=reporter_id, assignee_id=assignee_id,
        hazard_type="高处作业", location="A 区",
    )
    db.add(h)
    db.flush()
    db.add_all([
        HazardTimeline(hazard_id=h.id, action="上报", operator_id=reporter_id, note="提交"),
        HazardTimeline(hazard_id=h.id, action="派单", operator_id=1, note="指派"),
        HazardTimeline(hazard_id=h.id, action="验收驳回", operator_id=1, note="护栏缺失"),
        HazardTimeline(hazard_id=h.id, action="验收通过", operator_id=1, note="已整改"),
    ])
    db.commit()
    db.refresh(h)
    return h


def _drop_hazard(db, h):
    from sqlalchemy import text

    db.execute(text("DELETE FROM message_records WHERE kind='hazard_closed' AND ref_id=:i"), {"i": h.id})
    db.execute(text("DELETE FROM hazard_timeline WHERE hazard_id=:i"), {"i": h.id})
    db.execute(text("DELETE FROM hazard_ai_analysis WHERE hazard_id=:i"), {"i": h.id})
    db.execute(text("DELETE FROM hazards WHERE id=:i"), {"i": h.id})
    db.commit()


def test_record_hazard_closed_idempotent(db_session):
    h = _make_hazard(db_session)
    try:
        rid1 = record_service.record_hazard_closed(db_session, h)
        rid2 = record_service.record_hazard_closed(db_session, h)
        assert rid1 and rid1 == rid2                     # 唯一键 → 同一行
        row = record_service.get_record(db_session, {"id": 1, "role": "admin"}, rid1)
        assert row["kind"] == "hazard_closed"
        assert row["payload"]["accept"]["reject_count"] == 1
        assert len(row["payload"]["timeline"]) == 4
    finally:
        _drop_hazard(db_session, h)


def test_record_visibility_matrix(db_session):
    h = _make_hazard(db_session, reporter_id=101, assignee_id=202)
    try:
        rid = record_service.record_hazard_closed(db_session, h)
        admin = {"id": 1, "role": "admin"}
        sfo = {"id": 202, "role": "safety_officer"}
        other_sfo = {"id": 303, "role": "safety_officer"}
        own_emp = {"id": 101, "role": "employee"}
        other_emp = {"id": 404, "role": "employee"}

        assert record_service.get_record(db_session, admin, rid)
        assert record_service.get_record(db_session, sfo, rid)
        assert record_service.get_record(db_session, other_sfo, rid)      # 安全员看全部
        assert record_service.get_record(db_session, own_emp, rid)        # 上报人本人
        assert record_service.get_record(db_session, other_emp, rid) is None

        assert record_service.list_records(db_session, other_emp)["total"] == 0
        assert record_service.list_records(db_session, other_sfo)["total"] >= 1
    finally:
        _drop_hazard(db_session, h)


def test_record_contrib_snapshot(db_session):
    from src.models.knowledge_contrib import KnowledgeContribution

    c = KnowledgeContribution(
        title="单测贡献记录", content="x" * 1200, doc_type="procedures",
        contributor_id=1, status="已入库", chunk_count=3, source_kind="agent",
    )
    db_session.add(c)
    db_session.commit()
    db_session.refresh(c)
    try:
        rid = record_service.record_contrib(db_session, c)
        row = record_service.get_record(db_session, {"id": 1, "role": "admin"}, rid)
        assert row["payload"]["contrib"]["source_kind"] == "agent"
        assert "Agent" in row["summary"]
    finally:
        from sqlalchemy import text

        db_session.execute(text("DELETE FROM message_records WHERE kind='kb_contrib' AND ref_id=:i"), {"i": c.id})
        db_session.execute(text("DELETE FROM knowledge_contrib_timeline WHERE contrib_id=:i"), {"i": c.id})
        db_session.execute(text("DELETE FROM knowledge_contributions WHERE id=:i"), {"i": c.id})
        db_session.commit()


# --------------------------------------------------------------------------- #
# 3) 业务流程接线（monkeypatch 截获推送，不污染真实收件箱）
# --------------------------------------------------------------------------- #
@pytest.fixture()
def captured(monkeypatch):
    from src.services import hazard_service

    box = {"push": [], "drop": [], "drop_users": []}
    monkeypatch.setattr(
        hazard_service.NotificationService, "push",
        staticmethod(lambda uid, msg, **kw: box["push"].append((uid, msg, kw))),
    )
    monkeypatch.setattr(
        hazard_service.NotificationService, "drop",
        staticmethod(lambda uid, **kw: box["drop"].append((uid, kw)) or 1),
    )
    monkeypatch.setattr(
        hazard_service.NotificationService, "drop_for_users",
        staticmethod(lambda uids, **kw: box["drop_users"].append((list(uids), kw)) or 3),
    )
    return box


def test_report_notifies_admins_only(db_session, captured):
    from src.schemas.hazard import HazardCreate
    from src.services.hazard_service import HazardService

    h = HazardService.create(db_session, HazardCreate(
        title="单测上报消息", description="用于验证上报只发 admin", level="一般",
    ), user_id=101)
    try:
        assert captured["push"], "上报应推送"
        assert all(kw.get("msg_type") == "report" for _, _, kw in captured["push"])
        assert all(kw.get("ttl") is None for _, _, kw in captured["push"])
    finally:
        from sqlalchemy import text

        db_session.execute(text("DELETE FROM hazard_timeline WHERE hazard_id=:i"), {"i": h.id})
        db_session.execute(text("DELETE FROM hazards WHERE id=:i"), {"i": h.id})
        db_session.commit()


def test_rectify_does_not_notify_reporter(db_session, captured):
    from src.schemas.hazard import RectifyRequest
    from src.services.hazard_service import HazardService

    h = _make_hazard(db_session, status="处理中", reporter_id=101, assignee_id=202)
    try:
        HazardService.rectify(db_session, h.id, RectifyRequest(rectify_measures="已加装护栏"), {"id": 202, "role": "safety_officer"})
        targets = [uid for uid, _, _ in captured["push"]]
        assert 101 not in targets                              # 上报人不再收「待验收」
        assert all(kw.get("msg_type") == "rectify" for _, _, kw in captured["push"])
    finally:
        _drop_hazard(db_session, h)


def test_accept_pass_clears_and_records(db_session, captured):
    from src.schemas.hazard import AcceptRequest
    from src.services.hazard_service import HazardService

    h = _make_hazard(db_session, status="待验收", reporter_id=101, assignee_id=202)
    try:
        HazardService.accept(db_session, h.id, AcceptRequest(approved=True, note="合格"), {"id": 1, "role": "admin"})

        # ① 清消息：三类（上报/整改/派单）+ 责任人处堆积的驳回
        assert captured["drop_users"], "闭环应清理 report/rectify/assign"
        assert set(captured["drop_users"][0][1]["types"]) == {"report", "rectify", "assign"}
        assert any(d[1].get("status") == "rejected" for d in captured["drop"])     # 清掉堆积驳回

        # ② 发消息：责任人验收通过 + 上报人和 admin 最终处理
        passed = [p for p in captured["push"] if p[2].get("status") == "passed"]
        finals = [p for p in captured["push"] if p[2].get("msg_type") == "final"]
        assert passed and passed[0][0] == 202
        assert any(uid == 101 for uid, _, _ in finals)                              # 上报人收到最终处理
        assert all(kw.get("ttl") == 86400 for _, _, kw in passed + finals)          # 1 天

        # ③ 记录落库
        rows = record_service.list_records(db_session, {"id": 1, "role": "admin"}, kind="hazard_closed")
        assert any(r["ref_id"] == h.id for r in rows["items"])
    finally:
        _drop_hazard(db_session, h)


def test_accept_reject_accumulates_without_final(db_session, captured):
    from src.schemas.hazard import AcceptRequest
    from src.services.hazard_service import HazardService

    h = _make_hazard(db_session, status="待验收", reporter_id=101, assignee_id=202)
    try:
        HazardService.accept(db_session, h.id, AcceptRequest(approved=False, note="缺挡脚板"), {"id": 1, "role": "admin"})
        assert len(captured["push"]) == 1
        uid, _, kw = captured["push"][0]
        assert uid == 202 and kw.get("status") == "rejected" and kw.get("ttl") is None
        assert not captured["drop_users"] and not captured["drop"]                  # 驳回不动消息
        rows = record_service.list_records(db_session, {"id": 1, "role": "admin"}, kind="hazard_closed")
        assert not any(r["ref_id"] == h.id for r in rows["items"])                  # 未闭环不写记录
    finally:
        _drop_hazard(db_session, h)
