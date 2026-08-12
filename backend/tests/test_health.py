"""冒烟测试：应用启动 + 健康检查。"""
from fastapi.testclient import TestClient

from src.main import app


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["app"] == "蜀道智能安全助手"
