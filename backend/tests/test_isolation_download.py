"""v2.0 大点 00 · 产物下载账号隔离回归测试。

覆盖（对照规划文档 §00 ③ 3.6「单测」表）：
- 无 token → 401
- 他人账号取 PPT → 403；取试卷 → 404（沿用原语义）
- 本人取 → 200，且下载名是业务名（主题 / 试卷标题），不含内部编号
- 未生成的 PPT → 400
- `file_path` 被篡改成 `../` → 404，不越出产物目录
- 两个账号同主题 → 产物路径不同（不再互相覆盖）
- 老静态目录 `/ppt-files/*`、`/exam-files/*` 已下线 → 404
- 历史通道 `/uploads/exam/*` 已封 → 403

设计约束：不依赖真实业务数据、不调 LLM、不写实时目录
（DB 用 fake Session / monkeypatch，文件一律落 pytest 的 tmp_path）。
"""
import types
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.core.db import get_db
from src.core.security import create_access_token
from src.main import app
from src.services import exam_export
from src.services.exam_service import ExamService
from src.services.ppt_service import PptService

OWNER, OTHER, THIRD = 17, 608, 999


# ---------- 工具 ----------

def _auth(uid: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(uid, 'employee')}"}


def _ppt_task(uid: int, tid: int, topic: str, filename: str, status: str = "已完成"):
    """构造一个 PptTask 替身（download_path/_artifact_dest 与 PptTaskOut 序列化用到的字段）。"""
    return types.SimpleNamespace(
        id=tid, user_id=uid, topic=topic, status=status,
        file_path=f"ppt_files/{filename}", template="business",
        outline=None, created_at=datetime(2026, 10, 23, 10, 0, 0),
    )


class _FakeDB:
    """只实现 PptTask 归属查询需要的 db.get(model, pk)。"""

    def __init__(self, tasks: dict):
        self._tasks = tasks

    def get(self, model, pk):  # noqa: ARG002 —— 模型类型无关，仅按主键取
        return self._tasks.get(pk)


@pytest.fixture()
def client():
    return TestClient(app)


def _client_with_db(tasks: dict) -> TestClient:
    app.dependency_overrides[get_db] = lambda: _FakeDB(tasks)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------- PPT ----------

def test_ppt_download_requires_token(client):
    assert client.get(f"/api/ppt/1/download").status_code == 401


def test_ppt_download_rejects_other_account(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.ppt_service.PPT_FILES_DIR", tmp_path)
    (tmp_path / "u17_t6_高处作业安全.pptx").write_bytes(b"pptx")
    task = _ppt_task(OWNER, 6, "高处作业安全", "u17_t6_高处作业安全.pptx")
    c = _client_with_db({6: task})
    resp = c.get("/api/ppt/6/download", headers=_auth(OTHER))
    assert resp.status_code == 403, resp.text


def test_ppt_download_owner_success(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.ppt_service.PPT_FILES_DIR", tmp_path)
    (tmp_path / "u17_t6_高处作业安全.pptx").write_bytes(b"pptx-bytes")
    task = _ppt_task(OWNER, 6, "高处作业安全", "u17_t6_高处作业安全.pptx")
    c = _client_with_db({6: task})
    resp = c.get("/api/ppt/6/download", headers=_auth(OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.content == b"pptx-bytes"
    ctype = resp.headers.get("content-type", "")
    assert "presentation" in ctype
    # Starlette 对非 ASCII 文件名用 RFC 5987 百分号编码，断言前先解码
    assert "高处作业安全" in unquote(resp.headers.get("content-disposition", ""))


def test_ppt_download_not_ready_returns_400(client, tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.ppt_service.PPT_FILES_DIR", tmp_path)
    task = _ppt_task(OWNER, 9, "安全生产法要点", "u17_t9_安全生产法要点.pptx", status="草稿")
    task.file_path = None
    c = _client_with_db({9: task})
    resp = c.get("/api/ppt/9/download", headers=_auth(OWNER))
    assert resp.status_code == 400, resp.text


def test_ppt_download_path_traversal_blocked(client, tmp_path, monkeypatch):
    """库里 file_path 被篡改成 ../ 时，不能读到产物目录之外的文件。"""
    monkeypatch.setattr("src.services.ppt_service.PPT_FILES_DIR", tmp_path / "ppt_files")
    (tmp_path / "ppt_files").mkdir()
    secret = tmp_path / "secret.pptx"
    secret.write_bytes(b"should-not-be-served")
    task = _ppt_task(OWNER, 11, "越权", "../../secret.pptx")
    c = _client_with_db({11: task})
    resp = c.get("/api/ppt/11/download", headers=_auth(OWNER))
    assert resp.status_code == 404, resp.text
    assert b"should-not-be-served" not in resp.content


def test_ppt_artifact_names_are_per_owner(tmp_path, monkeypatch):
    """同主题的两个任务（含跨账号）产物路径必须不同——这是覆盖问题的根因回归。"""
    monkeypatch.setattr("src.services.ppt_service.PPT_FILES_DIR", tmp_path)
    a = _ppt_task(OWNER, 6, "高处作业安全", "")
    b = _ppt_task(OTHER, 21, "高处作业安全", "")
    pa, pb = PptService._artifact_dest(a), PptService._artifact_dest(b)
    assert pa != pb
    assert pa.name.startswith(f"u{OWNER}_t6_")
    assert pb.name.startswith(f"u{OTHER}_t21_")


def test_ppt_static_mount_removed(client):
    resp = client.get("/ppt-files/高处作业安全.pptx")
    assert resp.status_code == 404, resp.text


# ---------- 试卷 ----------

def _paper(pid: str, uid: int, title: str, file_path: str | None, fmt: str = "md"):
    return types.SimpleNamespace(
        paper_id=pid, title=title, file_path=file_path, export_format=fmt,
        created_by=uid, status="已完成", exam_content="{}",
    )


def test_exam_download_rejects_other_account(client, monkeypatch):
    def _get_paper(self, db, user_id, paper_id):
        raise HTTPException(status_code=404, detail="试卷不存在")

    monkeypatch.setattr(ExamService, "get_paper", _get_paper)
    resp = client.get("/api/exam/papers/paper_x/download", headers=_auth(OTHER))
    assert resp.status_code == 404, resp.text


def test_exam_download_owner_success(client, tmp_path, monkeypatch):
    exam_dir = tmp_path / "exam_files"
    exam_dir.mkdir()
    (exam_dir / "paper_x.md").write_text("试卷内容", encoding="utf-8")
    monkeypatch.setattr(exam_export, "EXAM_FILES_DIR", exam_dir)

    def _get_paper(self, db, user_id, paper_id):
        return _paper("paper_x", OWNER, "安全知识测试", "exam_files/paper_x.md")

    monkeypatch.setattr(ExamService, "get_paper", _get_paper)
    resp = client.get("/api/exam/papers/paper_x/download?format=md", headers=_auth(OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.content.decode("utf-8") == "试卷内容"
    assert "安全知识测试" in unquote(resp.headers.get("content-disposition", ""))


def test_exam_download_history_path_compatible(client, tmp_path, monkeypatch):
    """历史记录 file_path=exam/paper_old.md（仍在 uploads/exam/）也要能下载。"""
    old_dir = tmp_path / "uploads" / "exam"
    old_dir.mkdir(parents=True)
    (old_dir / "paper_old.md").write_text("历史试卷", encoding="utf-8")
    monkeypatch.setattr(exam_export, "EXAM_FILES_DIR", tmp_path / "exam_files")
    monkeypatch.setattr("src.services.exam_service.settings.UPLOAD_DIR", str(tmp_path / "uploads"))

    def _get_paper(self, db, user_id, paper_id):
        return _paper("paper_old", OWNER, "历史试卷标题", "exam/paper_old.md")

    monkeypatch.setattr(ExamService, "get_paper", _get_paper)
    resp = client.get("/api/exam/papers/paper_old/download", headers=_auth(OWNER))
    assert resp.status_code == 200, resp.text
    assert resp.content.decode("utf-8") == "历史试卷"


def test_exam_static_mount_removed(client):
    resp = client.get("/exam-files/paper_d0fd10a7c52c.md")
    assert resp.status_code == 404, resp.text


def test_exam_legacy_uploads_channel_blocked(client):
    resp = client.get("/uploads/exam/paper_d0fd10a7c52c.docx")
    assert resp.status_code == 403, resp.text


# ---------- 数据层隔离不回归（与调研结论对齐） ----------

def test_ppt_detail_still_isolated(client, monkeypatch):
    task = _ppt_task(OWNER, 6, "高处作业安全", "u17_t6_高处作业安全.pptx")
    c = _client_with_db({6: task})
    assert c.get("/api/ppt/6", headers=_auth(OTHER)).status_code == 403


def test_chat_conversations_requires_token(client):
    assert client.get("/api/conversations").status_code == 401


def test_exam_papers_requires_token(client):
    assert client.get("/api/exam/papers").status_code == 401
