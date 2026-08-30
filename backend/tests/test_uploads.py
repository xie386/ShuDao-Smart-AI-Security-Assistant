"""文件上传测试：白名单/大小限制/鉴权/静态访问。

依赖：本机 MySQL（users 表），需登录 token。
"""
import io
import uuid

from fastapi.testclient import TestClient

from src.main import app


def _auth_headers() -> dict:
    """注册随机用户并返回 Bearer 头。"""
    with TestClient(app) as client:
        username = f"up_{uuid.uuid4().hex[:10]}"
        client.post(
            "/api/auth/register", json={"username": username, "password": "pass123456"}
        )
        resp = client.post(
            "/api/auth/login", json={"username": username, "password": "pass123456"}
        )
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


def _fake_jpg(size: int = 1024) -> io.BytesIO:
    """构造伪 jpg 字节流（真实 JPEG 魔数开头）。"""
    return io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * (size - 4))


def test_upload_image_success():
    with TestClient(app) as client:
        resp = client.post(
            "/api/uploads/image",
            headers=_auth_headers(),
            files={"file": ("test.jpg", _fake_jpg(), "image/jpeg")},
        )
        assert resp.status_code == 201, resp.text
        url = resp.json()["url"]
        assert url.startswith("/uploads/")
        # 静态文件可访问
        static = client.get(url)
        assert static.status_code == 200
        assert static.content[:4] == b"\xff\xd8\xff\xe0"


def test_upload_rejects_bad_extension():
    with TestClient(app) as client:
        resp = client.post(
            "/api/uploads/image",
            headers=_auth_headers(),
            files={"file": ("malware.exe", io.BytesIO(b"MZ..."), "application/x-msdownload")},
        )
        assert resp.status_code == 400


def test_upload_rejects_mime_mismatch():
    with TestClient(app) as client:
        resp = client.post(
            "/api/uploads/image",
            headers=_auth_headers(),
            files={"file": ("fake.png", _fake_jpg(), "image/jpeg")},
        )
        assert resp.status_code == 400  # 扩展名 png 但 MIME jpeg


def test_upload_rejects_too_large():
    with TestClient(app) as client:
        resp = client.post(
            "/api/uploads/image",
            headers=_auth_headers(),
            files={"file": ("big.jpg", _fake_jpg(11 * 1024 * 1024), "image/jpeg")},
        )
        assert resp.status_code == 413


def test_upload_requires_auth():
    with TestClient(app) as client:
        resp = client.post(
            "/api/uploads/image",
            files={"file": ("a.jpg", _fake_jpg(), "image/jpeg")},
        )
        assert resp.status_code == 401
