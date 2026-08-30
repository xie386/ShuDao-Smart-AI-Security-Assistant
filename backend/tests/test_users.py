"""用户列表接口测试（D5 A2：派单选择责任人）。

覆盖：
- admin 可调 /api/users，返回列表且排除自己（id）
- 多角色过滤 role=safety_officer,employee + limit 生效
- 非 admin（employee）访问 403
"""
import uuid

from fastapi.testclient import TestClient

from src.main import app


def _mk_user(client: TestClient, prefix: str, role: str = "employee") -> dict:
    """直插库建用户并返回登录 headers（测试专用，避开注册接口角色限制）。"""
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


def test_users_admin_list_excludes_self():
    """admin 拿用户列表：返回非空、排除自己、不含 hashed_password。"""
    with TestClient(app) as client:
        admin = _mk_user(client, "adm", role="admin")
        resp = client.get("/api/users", headers=admin["headers"])
        assert resp.status_code == 200, resp.text
        users = resp.json()
        assert isinstance(users, list) and len(users) > 0
        assert admin["id"] not in [u["id"] for u in users], "列表不应包含自己"
        assert "hashed_password" not in users[0], "不得泄露密码哈希"


def test_users_role_filter_and_limit():
    """多角色过滤 + limit：只返回指定角色，数量受 limit 约束。"""
    with TestClient(app) as client:
        admin = _mk_user(client, "adm2", role="admin")
        _mk_user(client, "emp1", role="employee")
        _mk_user(client, "saf1", role="safety_officer")

        resp = client.get(
            "/api/users?role=safety_officer,employee&limit=10", headers=admin["headers"]
        )
        assert resp.status_code == 200, resp.text
        users = resp.json()
        assert len(users) <= 10
        roles = {u["role"] for u in users}
        assert roles <= {"safety_officer", "employee"}, f"出现非法角色: {roles}"


def test_users_forbidden_for_employee():
    """非 admin 访问 /api/users → 403。"""
    with TestClient(app) as client:
        emp = _mk_user(client, "emp2", role="employee")
        resp = client.get("/api/users", headers=emp["headers"])
        assert resp.status_code == 403, resp.text


def test_users_search_by_id_and_name():
    """搜索：按 ID 精确匹配；按姓名/用户名模糊匹配（含 limit 截断外的用户）。"""
    with TestClient(app) as client:
        admin = _mk_user(client, "adm3", role="admin")
        target = _mk_user(client, "saf_smith", role="safety_officer")

        # 按 ID 精确搜索
        resp = client.get(f"/api/users?role=safety_officer&q={target['id']}", headers=admin["headers"])
        assert resp.status_code == 200, resp.text
        ids = [u["id"] for u in resp.json()]
        assert target["id"] in ids, "ID 搜索应命中目标用户"

        # 按用户名模糊搜索（模拟"搜到 50 名以外"的场景）
        resp2 = client.get(
            f"/api/users?role=safety_officer&q=saf_smith&limit=100", headers=admin["headers"]
        )
        assert resp2.status_code == 200, resp2.text
        usernames = [u["username"] for u in resp2.json()]
        assert any("saf_smith" in u for u in usernames), "姓名/用户名模糊搜索应命中"
