"""隐患模块测试（H01–H06 全流程闭环 + RBAC + 状态机校验）。

覆盖：
- 上报 → 派单 → 整改 → 验收通过 → 已闭环（完整闭环，admin 驱动）
- 验收驳回 → 已驳回 → 重新派单 → 处理中
- RBAC：employee 派单/验收 403；非责任人整改 403
- 状态机：未派单直接整改 400、重复验收 400
- 列表：分页 / 等级筛选 / 只看我的
"""
import uuid

from fastapi.testclient import TestClient

from src.main import app


def _register(client: TestClient, prefix: str, role: str = "employee") -> dict:
    """注册/插入用户并返回 {headers, id}。role=admin 时直插库（测试专用）。"""
    from src.core.db import SessionLocal
    from src.core.security import hash_password
    from src.models.user import User

    username = f"{prefix}_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = User(
            username=username,
            hashed_password=hash_password("pass123456"),
            role=role,
        )
        db.add(user)
        db.commit()
        uid = user.id
    token = client.post(
        "/api/auth/login", json={"username": username, "password": "pass123456"}
    ).json()["access_token"]
    return {"headers": {"Authorization": f"Bearer {token}"}, "id": uid}


def _create_hazard(client: TestClient, headers: dict, **overrides) -> dict:
    payload = {
        "title": "脚手架护栏缺失",
        "description": "三层脚手架作业平台外侧未安装防护栏杆，存在坠落风险。",
        "level": "较大",
        "hazard_type": "物的不安全状态",
        "location": "3号厂房东侧",
        **overrides,
    }
    resp = client.post("/api/hazards", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_full_closure_flow():
    """上报→派单→整改→验收通过→已闭环 完整闭环。"""
    with TestClient(app) as client:
        reporter = _register(client, "rep")
        assignee = _register(client, "asg")
        admin = _register(client, "adm", role="admin")
        h = _create_hazard(client, reporter["headers"])

        # 上报后详情：待处理 + 时间线[上报]
        detail = client.get(f"/api/hazards/{h['id']}", headers=reporter["headers"]).json()
        assert detail["status"] == "待处理"
        assert [t["action"] for t in detail["timeline"]] == ["上报"]

        # 派单（admin）→ 处理中
        resp = client.post(
            f"/api/hazards/{h['id']}/assign",
            json={"assignee_id": assignee["id"], "due_date": "2026-08-20T00:00:00"},
            headers=admin["headers"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "处理中"
        assert resp.json()["assignee_id"] == assignee["id"]

        # 整改（责任人）→ 待验收
        resp = client.post(
            f"/api/hazards/{h['id']}/rectify",
            json={"rectify_measures": "已安装防护栏杆并加固，复查合格。"},
            headers=assignee["headers"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "待验收"
        assert resp.json()["rectify_measures"].startswith("已安装防护栏杆")

        # 验收通过（admin）→ 已闭环
        resp = client.post(
            f"/api/hazards/{h['id']}/accept",
            json={"approved": True, "note": "整改到位，验收通过"},
            headers=admin["headers"],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "已闭环"

        # 时间线完整：上报/派单/整改反馈/验收通过
        detail = client.get(f"/api/hazards/{h['id']}", headers=reporter["headers"]).json()
        actions = [t["action"] for t in detail["timeline"]]
        assert actions == ["上报", "派单", "整改反馈", "验收通过"]

        # 已闭环后再验收 → 400
        resp = client.post(
            f"/api/hazards/{h['id']}/accept",
            json={"approved": True},
            headers=admin["headers"],
        )
        assert resp.status_code == 400


def test_reject_then_reassign():
    """验收驳回 → 已驳回 → 重新派单 → 处理中。"""
    with TestClient(app) as client:
        reporter = _register(client, "rep")
        assignee = _register(client, "asg")
        admin = _register(client, "adm", role="admin")
        h = _create_hazard(client, reporter["headers"])

        client.post(
            f"/api/hazards/{h['id']}/assign",
            json={"assignee_id": assignee["id"]},
            headers=admin["headers"],
        )
        client.post(
            f"/api/hazards/{h['id']}/rectify",
            json={"rectify_measures": "简单处理了一下"},
            headers=assignee["headers"],
        )
        # 验收驳回 → 已驳回
        resp = client.post(
            f"/api/hazards/{h['id']}/accept",
            json={"approved": False, "note": "整改不彻底，需重新处理"},
            headers=admin["headers"],
        )
        assert resp.json()["status"] == "已驳回"

        # 重新派单 → 处理中
        resp = client.post(
            f"/api/hazards/{h['id']}/assign",
            json={"assignee_id": assignee["id"]},
            headers=admin["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "处理中"


def test_rbac_and_state_machine():
    """RBAC：employee 不能派单/验收；非责任人不能整改；非法流转 400。"""
    with TestClient(app) as client:
        reporter = _register(client, "rep")
        assignee = _register(client, "asg")
        outsider = _register(client, "out")
        admin = _register(client, "adm", role="admin")
        h = _create_hazard(client, reporter["headers"])

        # employee 派单 → 403
        resp = client.post(
            f"/api/hazards/{h['id']}/assign",
            json={"assignee_id": assignee["id"]},
            headers=reporter["headers"],
        )
        assert resp.status_code == 403

        # 非责任人整改（待处理，assignee 为空）→ 403（权限先于状态机）
        resp = client.post(
            f"/api/hazards/{h['id']}/rectify",
            json={"rectify_measures": "直接整改处理"},
            headers=outsider["headers"],
        )
        assert resp.status_code == 403

        # admin 整改未派单隐患 → 400（状态机拦截：待处理不允许整改）
        resp = client.post(
            f"/api/hazards/{h['id']}/rectify",
            json={"rectify_measures": "直接整改处理"},
            headers=admin["headers"],
        )
        assert resp.status_code == 400

        # employee 验收 → 403（RBAC 先于业务）
        resp = client.post(
            f"/api/hazards/{h['id']}/accept",
            json={"approved": True},
            headers=reporter["headers"],
        )
        assert resp.status_code == 403

        # 直插库模拟已派单（处理中 + assignee）
        from src.core.db import SessionLocal
        from src.models.hazard import Hazard

        with SessionLocal() as db:
            row = db.get(Hazard, h["id"])
            row.status = "处理中"
            row.assignee_id = assignee["id"]
            db.commit()

        # 非责任人整改 → 403
        resp = client.post(
            f"/api/hazards/{h['id']}/rectify",
            json={"rectify_measures": "我是路人我来改"},
            headers=outsider["headers"],
        )
        assert resp.status_code == 403

        # 责任人整改 → 200
        resp = client.post(
            f"/api/hazards/{h['id']}/rectify",
            json={"rectify_measures": "责任人已完成整改"},
            headers=assignee["headers"],
        )
        assert resp.status_code == 200


def test_list_pagination_filter_mine():
    """分页 / 等级筛选 / 只看我上报的。"""
    with TestClient(app) as client:
        reporter = _register(client, "rep")
        other = _register(client, "oth")
        for i in range(3):
            _create_hazard(client, reporter["headers"], title=f"测试隐患{i}")
        _create_hazard(client, other["headers"], title="别人的隐患")

        # 分页
        lst = client.get(
            "/api/hazards?page=1&page_size=2", headers=reporter["headers"]
        ).json()
        assert lst["page"] == 1 and lst["page_size"] == 2
        assert len(lst["items"]) == 2

        # 等级筛选
        lv = client.get("/api/hazards?level=较大", headers=reporter["headers"]).json()
        assert lv["total"] >= 4
        assert all(i["level"] == "较大" for i in lv["items"])

        # 只看我的
        mine = client.get("/api/hazards?mine=true", headers=reporter["headers"]).json()
        assert mine["total"] == 3
        assert all(i["reporter_id"] == reporter["id"] for i in mine["items"])
