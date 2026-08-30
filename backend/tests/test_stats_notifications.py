"""H07 统计 / H08 通知接口测试。

- stats：空表也应返回完整结构（total=0、rectify_rate=0、trend 7 天）；
- notifications：Redis 未启动时优雅返回空列表（不 500）。
"""

import uuid

from fastapi.testclient import TestClient

from src.main import app


def _auth_headers() -> dict:
    with TestClient(app) as client:
        username = f"st_{uuid.uuid4().hex[:10]}"
        client.post(
            "/api/auth/register", json={"username": username, "password": "pass123456"}
        )
        resp = client.post(
            "/api/auth/login", json={"username": username, "password": "pass123456"}
        )
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_hazard_stats_structure():
    with TestClient(app) as client:
        resp = client.get("/api/stats/hazards", headers=_auth_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("total", "by_level", "by_type", "rectify_rate", "pending", "trend"):
            assert key in body
        assert isinstance(body["rectify_rate"], float)  # 0~1（不依赖空表，其他测试可能已写入数据）
        assert len(body["trend"]) == 7  # 近 7 天趋势点


def test_stats_requires_auth():
    with TestClient(app) as client:
        assert client.get("/api/stats/hazards").status_code == 401


def test_notifications_graceful_without_redis():
    """Redis 未启动时通知接口应优雅返回空，不 500。"""
    with TestClient(app) as client:
        resp = client.get("/api/notifications", headers=_auth_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []
