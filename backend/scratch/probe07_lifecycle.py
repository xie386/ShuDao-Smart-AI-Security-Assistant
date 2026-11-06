"""07 真机验收：隐患全生命周期 → 消息生命周期 + 记录落库 + 可见性。

用真实 Redis/MySQL，但只用一个临时隐患，结束时把该隐患的所有消息与数据清干净。
用法：PYTHONPATH= C:/python/python.exe scratch/probe07_lifecycle.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from src.core import redis_boot, redis_client as redis_client_mod  # noqa: E402
from src.core.db import SessionLocal, engine  # noqa: E402
from src.core.security import get_current_user  # noqa: E402
from src.main import app  # noqa: E402
from src.models.user import User  # noqa: E402
from src.services import notification_service as N  # noqa: E402

print("Redis:", redis_boot.ensure()["state"])
redis_client_mod.reset()
db = SessionLocal()
admin = db.scalars(select(User).where(User.role == "admin").limit(1)).first()
sfo = db.scalars(select(User).where(User.role == "safety_officer").limit(1)).first()
emp = db.scalars(select(User).where(User.role == "employee").limit(1)).first()
if not (admin and sfo and emp):
    print("库里缺少 admin/safety_officer/employee 账号，无法验收")
    sys.exit(2)

print(f"账号：admin=#{admin.id} sfo=#{sfo.id} employee=#{emp.id}")
ADMINS = db.execute(select(User.id).where(User.role == "admin")).scalars().all()
print(f"admin 总数：{len(ADMINS)}")


def as_user(uid):
    return {"id": uid, "username": f"probe{uid}", "role": "admin" if uid == admin.id else "employee"}


def msgs_of(uid, hid=None):
    items = N.list_for(uid, limit=100)
    return [m for m in items if hid is None or m["hazard_id"] == hid]


def brief(uid, hid):
    out = []
    for m in msgs_of(uid, hid):
        exp = m["expire_at"]
        left = f"{int((exp - time.time()) / 60)}min" if exp else "∞"
        out.append(f"{m['type']}{'/' + m['status'] if m['status'] else ''}({left})")
    return out


hid = None
record_id = None
try:
    # ---------------- 1) 上报 ----------------
    app.dependency_overrides[get_current_user] = lambda: as_user(emp.id)
    with TestClient(app) as c:
        r = c.post("/api/hazards", json={
            "title": "探针07：消息与记录全流程",
            "description": "由 probe07_lifecycle.py 生成，验证 07 消息生命周期与记录，随后删除。",
            "level": "一般", "hazard_type": "高处作业", "location": "探针区",
        })
        assert r.status_code == 201, r.text
        hid = r.json()["id"]
        print(f"\n[1] 上报 #{hid} → admin 消息：{brief(admin.id, hid)}")
        assert any(m["type"] == "report" for m in msgs_of(admin.id, hid))

        # ---------------- 2) 派单 ----------------
        app.dependency_overrides[get_current_user] = lambda: as_user(admin.id)
        r = c.post(f"/api/hazards/{hid}/assign", json={"assignee_id": sfo.id})
        assert r.status_code == 200, r.text
        print(f"[2] 派单给 #{sfo.id} → sfo 消息：{brief(sfo.id, hid)}")
        assert any(m["type"] == "assign" for m in msgs_of(sfo.id, hid))

        # ---------------- 3) 整改反馈 ----------------
        app.dependency_overrides[get_current_user] = lambda: {
            "id": sfo.id, "username": sfo.username, "role": "safety_officer"
        }
        r = c.post(f"/api/hazards/{hid}/rectify", json={"rectify_measures": "已加装护栏与挡脚板"})
        assert r.status_code == 200, r.text
        print(f"[3] 整改 → admin 消息：{brief(admin.id, hid)} | 上报人：{brief(emp.id, hid)}")
        assert any(m["type"] == "rectify" for m in msgs_of(admin.id, hid))
        assert not any(m["type"] in ("rectify", "accept") for m in msgs_of(emp.id, hid)), "上报人不应收到整改/验收"

        # ---------------- 4) 验收驳回 ----------------
        app.dependency_overrides[get_current_user] = lambda: as_user(admin.id)
        r = c.post(f"/api/hazards/{hid}/accept", json={"approved": False, "note": "挡脚板高度不足"})
        assert r.status_code == 200, r.text
        print(f"[4] 驳回 → sfo 消息：{brief(sfo.id, hid)} | admin 新增：{brief(admin.id, hid)}")
        assert any(m["type"] == "accept" and m["status"] == "rejected" for m in msgs_of(sfo.id, hid))
        assert not any(m["type"] == "final" for m in msgs_of(admin.id, hid)), "驳回不应发最终处理"

        # ---------------- 5) 驳回后必须重新派单 → 再整改 → 通过 ----------------
        # 真实流转：已驳回 --派单--> 处理中 --整改--> 待验收 --验收--> 已闭环
        app.dependency_overrides[get_current_user] = lambda: as_user(admin.id)
        r = c.post(f"/api/hazards/{hid}/assign", json={"assignee_id": sfo.id})
        assert r.status_code == 200, r.text

        app.dependency_overrides[get_current_user] = lambda: {
            "id": sfo.id, "username": sfo.username, "role": "safety_officer"
        }
        r = c.post(f"/api/hazards/{hid}/rectify", json={"rectify_measures": "挡脚板加高至 18cm 并复检"})
        assert r.status_code == 200, r.text

        app.dependency_overrides[get_current_user] = lambda: as_user(admin.id)
        r = c.post(f"/api/hazards/{hid}/accept", json={"approved": True, "note": "复检合格"})
        assert r.status_code == 200, r.text
        admin_left = brief(admin.id, hid)
        sfo_left = brief(sfo.id, hid)
        emp_left = brief(emp.id, hid)
        print(f"[5] 通过 → admin：{admin_left}\n            sfo：{sfo_left}\n            employee：{emp_left}")
        assert not any(t.startswith(("report", "rectify", "assign")) for t in admin_left), "闭环应删上报/整改/派单"
        assert not any("rejected" in t for t in sfo_left), "闭环应清掉堆积的驳回"
        assert any(t.startswith("accept/passed") for t in sfo_left)
        assert any(t.startswith("final") for t in emp_left)
        # 1 天 TTL
        passed = [m for m in msgs_of(sfo.id, hid) if m["status"] == "passed"][0]
        left_h = (passed["expire_at"] - time.time()) / 3600
        print(f"    通过消息剩余有效期 ≈ {left_h:.1f} 小时")
        assert 23.5 < left_h <= 24.1

        # ---------------- 6) 接口：列表 / 未读 / 已读 / 记录可见性 ----------------
        r = c.get("/api/notifications?limit=100")
        d = r.json()
        print(f"[6] GET /notifications: total={d['total']} unread={d['unread']}")
        before = d["unread"]
        r = c.post("/api/notifications/read", json={"all": True})
        print(f"    标已读后 unread={r.json()['unread']}")
        assert r.json()["unread"] == 0

        reps = c.get("/api/records?page=1&page_size=50").json()
        rec = [x for x in reps["items"] if x["kind"] == "hazard_closed" and x["ref_id"] == hid]
        print(f"[7] admin 记录列表：total={reps['total']}，本隐患记录 {len(rec)} 条")
        assert rec, "闭环应写入记录"
        record_id = rec[0]["id"]
        detail = c.get(f"/api/records/{record_id}").json()
        p = detail["payload"]
        print(f"    记录快照：时间线 {len(p['timeline'])} 步、驳回 {p['accept']['reject_count']} 次、AI={bool(p.get('ai'))}")

        app.dependency_overrides[get_current_user] = lambda: {
            "id": sfo.id, "username": sfo.username, "role": "safety_officer"
        }
        d = c.get("/api/records?page=1&page_size=50").json()
        hit = [x for x in d["items"] if x["kind"] == "hazard_closed" and x["ref_id"] == hid]
        print(f"[8] safety_officer 记录列表：total={d['total']}，本隐患 {len(hit)} 条（应为 1）")
        assert hit

        other_emp = db.scalars(
            select(User).where(User.role == "employee", User.id != emp.id).limit(1)
        ).first() or emp
        app.dependency_overrides[get_current_user] = lambda: {
            "id": other_emp.id, "username": "other", "role": "employee"
        }
        d2 = c.get("/api/records?page=1&page_size=50").json()
        hit2 = [x for x in d2["items"] if x["kind"] == "hazard_closed" and x["ref_id"] == hid]
        print(f"[9] 其他 employee 记录列表：total={d2['total']}，本隐患 {len(hit2)} 条（应为 0）")
        assert not hit2, "其他员工不应看到别人上报的闭环记录"
        assert c.get(f"/api/records/{record_id}").status_code == 404

    print("\n全部断言通过")
finally:
    app.dependency_overrides.clear()
    # 清理：本隐患产生的消息（admin/责任人/上报人）
    if hid:
        for uid in set(ADMINS) | {sfo.id, emp.id}:
            try:
                N.drop(uid, hazard_id=hid)
            except Exception:  # noqa: BLE001
                pass
    with engine.begin() as conn:
        if hid:
            conn.execute(text("DELETE FROM message_records WHERE kind='hazard_closed' AND ref_id=:i"), {"i": hid})
            conn.execute(text("DELETE FROM hazard_ai_analysis WHERE hazard_id=:i"), {"i": hid})
            conn.execute(text("DELETE FROM hazard_timeline WHERE hazard_id=:i"), {"i": hid})
            conn.execute(text("DELETE FROM hazards WHERE id=:i"), {"i": hid})
    print(f"已清理探针隐患 #{hid} 及其消息/记录")
    db.close()
    redis_boot.stop()
