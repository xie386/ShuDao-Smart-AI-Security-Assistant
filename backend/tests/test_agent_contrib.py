"""v2.0 大点 04：Agent 渠道（身份/提交）、方法论读写、网搜安全边界的单测。

不连外网、不跑 Playwright、不碰真实 uploads：
- `url_allowed` 只做 URL/主机校验（DNS 解析失败即拒绝）；
- Playwright 采集用 `_plan_queries`/`_collect` 的替身绕过。
"""
import hashlib
import json
from pathlib import Path

import pytest

from src.ai import agent_scout
from src.services import agent_docs, agent_contrib_service


# --------------------------------------------------------------------------- #
# 安全边界
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/windows/win.ini",
        "ftp://example.com/a.pdf",
        "http://localhost:8000/health",
        "http://127.0.0.1/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_url_denied(url):
    ok, why = agent_scout.url_allowed(url)
    assert ok is False and why


def test_url_allows_public_domain():
    ok, why = agent_scout.url_allowed("https://www.mem.gov.cn/")
    # 网络不可用/DNS 失败时也必须是"拒绝"而不是崩溃；可解析则放行
    assert isinstance(ok, bool)
    assert (ok is True) or ("解析" in why or "校验" in why)


def test_should_scout_rules(monkeypatch):
    monkeypatch.setattr(agent_scout.settings, "KB_AGENT_SCOUT_ENABLED", True)
    assert agent_scout.should_scout({"mode": "chat"}, 0) is False
    assert agent_scout.should_scout({"mode": "full"}, 4) is False
    assert agent_scout.should_scout({"mode": "full"}, 0) is True
    assert agent_scout.should_scout({"mode": "full", "retrieval_degraded": {"kind": "retrieval"}}, 3) is True
    monkeypatch.setattr(agent_scout.settings, "KB_AGENT_SCOUT_ENABLED", False)
    assert agent_scout.should_scout({"mode": "full"}, 0) is False


def test_kick_off_is_idempotent(monkeypatch):
    import time as _t

    started = []

    def fake_run(conv, q):
        started.append((conv, q))
        _t.sleep(0.4)
        return {}

    monkeypatch.setattr(agent_scout, "run", fake_run)
    assert agent_scout.kick_off(1, "测试问题") is True
    assert agent_scout.kick_off(1, "测试问题") is False   # 同一 key 正在跑
    _t.sleep(0.6)
    assert agent_scout.kick_off(1, "测试问题") is True     # 跑完可以再来
    _t.sleep(0.6)
    assert len(started) >= 2


# --------------------------------------------------------------------------- #
# 身份与提交
# --------------------------------------------------------------------------- #
@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    """上传目录与知识库目录都指向临时路径。"""
    uploads = tmp_path / "uploads"
    kb = tmp_path / "knowledge_src"
    (kb / "procedures").mkdir(parents=True)
    monkeypatch.setattr(agent_contrib_service.settings, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(
        agent_contrib_service.NotificationService, "push", staticmethod(lambda *a, **k: None)
    )
    return {"uploads": uploads, "kb": kb}


def test_authenticate_ok_and_bad(db_session, agent_env):
    from src.models.agent import AgentClient

    import uuid

    raw = "test-key-" + uuid.uuid4().hex
    name = "unit_agent_" + uuid.uuid4().hex[:6]
    row = AgentClient(name=name, api_key_hash=hashlib.sha256(raw.encode()).hexdigest(),
                      model="m", scopes="contrib:submit", enabled=1)
    db_session.add(row)
    db_session.commit()

    got = agent_contrib_service.authenticate(db_session, raw)
    assert got["name"] == name
    with pytest.raises(Exception):
        agent_contrib_service.authenticate(db_session, "wrong")

    db_session.delete(row)
    db_session.commit()


def test_submit_agent_creates_pending_contribution(db_session, agent_env):
    from src.models.knowledge_contrib import KnowledgeContribution

    md = "# 有限空间作业要求\n\n" + ("第一条 应办理作业票。" * 40)
    contrib = agent_contrib_service.submit_agent(
        db_session,
        title="有限空间作业要求",
        doc_type="procedures",
        intro="Agent 采集",
        md=md,
        agent={"id": 1, "name": "knowledge_scout", "model": "deepseek-v4-flash"},
        origin_urls=["https://www.mem.gov.cn/a.html"],
        origin_filename="有限空间作业要求.md",
    )
    assert contrib.status == "待审核" and contrib.source_kind == "agent"
    assert json.loads(contrib.origin_urls)[0].startswith("https://www.mem.gov.cn")
    assert Path(agent_contrib_service.settings.UPLOAD_DIR, contrib.file_path).is_file()

    from src.models.knowledge_contrib import KnowledgeContribTimeline

    tl = db_session.query(KnowledgeContribTimeline).filter_by(contrib_id=contrib.id).all()
    assert tl and tl[0].actor_kind == "agent" and tl[0].operator_id is None

    # 清理（避免污染开发库）
    for row in tl:
        db_session.delete(row)
    db_session.delete(contrib)
    db_session.commit()


def test_submit_agent_rejects_short_md(db_session, agent_env):
    with pytest.raises(Exception):
        agent_contrib_service.submit_agent(
            db_session, title="短", doc_type="laws", intro="", md="太短",
            agent={"id": 1, "name": "knowledge_scout", "model": "m"},
        )


# --------------------------------------------------------------------------- #
# 方法论读写
# --------------------------------------------------------------------------- #
@pytest.fixture()
def docs_dir(tmp_path, monkeypatch):
    d = tmp_path / "Methodology"
    d.mkdir()
    (d / "Methodology.md").write_text("# 方法论 v1\n\n- 优先站点：应急管理部\n", encoding="utf-8")
    (d / "cases.md").write_text("2026-10-26 | https://mem.gov.cn | 站内搜索 | 成功 | 附件是正文\n",
                                encoding="utf-8")
    monkeypatch.setattr(agent_docs.settings, "KB_AGENT_DOCS_DIR", str(d))
    return d


def test_docs_read_version_write_backup(docs_dir):
    r = agent_docs.read(agent_docs.METHODOLOGY)
    assert r["exists"] is True and r["version"] != "none"

    out = agent_docs.write(agent_docs.METHODOLOGY, "# 方法论 v2\n\n- 新增：黑名单\n")
    assert out["version"] != r["version"]
    assert (docs_dir / "Methodology.md.bak").read_text(encoding="utf-8").startswith("# 方法论 v1")
    assert agent_docs.version() == out["version"]


def test_docs_append_case_dedup(docs_dir):
    line = "2026-10-27 | https://x.gov.cn | 直接访问 | 失败(403) | 需登录"
    assert agent_docs.append_case(line)["appended"] is True
    assert agent_docs.append_case(line)["appended"] is False
    assert "403" in (docs_dir / "cases.md").read_text(encoding="utf-8")


def test_docs_read_missing_falls_back_to_example(tmp_path, monkeypatch):
    d = tmp_path / "M2"
    d.mkdir()
    (d / "Methodology.md.example").write_text("# 示例模板\n", encoding="utf-8")
    monkeypatch.setattr(agent_docs.settings, "KB_AGENT_DOCS_DIR", str(d))
    r = agent_docs.read(agent_docs.METHODOLOGY)
    assert r["exists"] is False and r["from_example"] is True and "# 示例模板" in r["content"]


# --------------------------------------------------------------------------- #
# 缺口识别 + 模型主动提交（v2.0 大点 04 补强）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer, want",
    [
        ("依据现有资料无法确认矿洞作业的装备清单。", True),
        ("本轮参考资料中没有针对地下矿山的装备配备要求。", True),
        ("该场景未覆盖，建议补充。", True),
        ("高处作业必须系挂安全带，做到高挂低用。[来源: xxx]", False),
        ("短", False),
    ],
)
def test_answer_declares_gap(answer, want):
    assert agent_scout.answer_declares_gap(answer) is want


def test_should_scout_triggers_on_declared_gap(monkeypatch):
    monkeypatch.setattr(agent_scout.settings, "KB_AGENT_SCOUT_ENABLED", True)
    prepared = {"mode": "full"}
    assert agent_scout.should_scout(prepared, 4, answer="高处作业要求见资料。") is False
    assert agent_scout.should_scout(
        prepared, 4, answer="本轮参考资料中没有针对矿洞的装备清单，依据现有资料无法确认。"
    ) is True


def test_report_gap_tool_accepts_once(monkeypatch):
    from src.ai import chat_agent

    calls = []

    def fake_kick(conv_id, question):
        calls.append((conv_id, question))
        return True

    monkeypatch.setattr(agent_scout, "kick_off", fake_kick)
    ctx = {"conversation_id": 7, "question": "矿洞作业要配哪些装备？", "gap_calls": 0}
    monkeypatch.setattr(chat_agent, "_current_run_ctx", lambda: ctx)

    tools = {t.name: t for t in chat_agent._make_tools()}
    gap_tool = tools["report_knowledge_gap"]

    out1 = gap_tool.func("缺少矿洞装备配备要求")
    out2 = gap_tool.func("再提交一次")
    assert "已受理" in out1 and calls == [(7, "矿洞作业要配哪些装备？")]
    assert "已提交过一次" in out2 and len(calls) == 1     # 整轮只允许一次
    assert ctx["gap_report"]["accepted"] is True
