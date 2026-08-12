"""认证闭环测试：注册（硬编码 employee）→ 登录 → /me 鉴权 → 无 token 拒绝。

依赖：本机 MySQL（shudao_safety 库）可用，users 表已由 P3 schema 建好。
"""
import uuid

from fastapi.testclient import TestClient

from src.main import app


def _new_username() -> str:
    return f"tester_{uuid.uuid4().hex[:10]}"


def test_register_returns_employee_role():
    """安全约定：注册接口不接受 role，返回角色必须为 employee。"""
    with TestClient(app) as client:
        resp = client.post(
            "/api/auth/register",
            json={"username": _new_username(), "password": "pass123456", "real_name": "测试员"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["role"] == "employee"
        assert "hashed_password" not in body  # 不泄露哈希


def test_register_duplicate_username_conflict():
    username = _new_username()
    payload = {"username": username, "password": "pass123456"}
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=payload).status_code == 201
        resp = client.post("/api/auth/register", json=payload)
        assert resp.status_code == 409


def test_login_then_me():
    username, password = _new_username(), "pass123456"
    with TestClient(app) as client:
        client.post("/api/auth/register", json={"username": username, "password": password})

        resp = client.post("/api/auth/login", json={"username": username, "password": password})
        assert resp.status_code == 200, resp.text
        token = resp.json()["access_token"]
        assert token

        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == username


def test_me_without_token_rejected():
    with TestClient(app) as client:
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401


def test_login_wrong_password_rejected():
    with TestClient(app) as client:
        client.post(
            "/api/auth/register", json={"username": _new_username(), "password": "pass123456"}
        )
        resp = client.post(
            "/api/auth/login", json={"username": _new_username(), "password": "wrong-pass"}
        )
        # 用户名不存在与密码错误统一 401，不泄露账号是否存在
        assert resp.status_code == 401
