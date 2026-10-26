"""案例库回写（审核结论 → cases.md）：单元测试。"""
from __future__ import annotations

import pytest

from src.services import agent_docs


@pytest.fixture()
def case_file(tmp_path, monkeypatch):
    f = tmp_path / "cases.md"
    f.write_text(
        "# Agent 采集案例库\n\n"
        "2026-10-26 | https://a.gov.cn/x.html | 缺口「甲」 | 成功（2 篇，1500 字） | 已提交待审 #7\n"
        "2026-10-26 | https://b.gov.cn/y.html | 缺口「乙」 | 成功（1 篇，1200 字） | 已提交待审 #8\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_docs, "path_of", lambda name: f)
    return f


def test_update_case_rewrites_pending_line(case_file):
    r = agent_docs.update_case(7, "已驳回", "缺口叙述，不是知识")
    assert r["updated"] is True
    text = case_file.read_text(encoding="utf-8")
    line7 = [ln for ln in text.splitlines() if "#7" in ln][0]
    assert "审核驳回 #7：缺口叙述，不是知识" in line7
    assert "已提交待审 #7" not in line7
    # 另一条不受影响
    assert "已提交待审 #8" in text


def test_update_case_approve_has_no_reason_suffix(case_file):
    agent_docs.update_case(7, "已入库")
    line7 = [ln for ln in case_file.read_text(encoding="utf-8").splitlines() if "#7" in ln][0]
    assert line7.endswith("审核通过 #7")


def test_update_case_appends_when_line_missing(case_file):
    r = agent_docs.update_case(99, "已驳回", "标题重复", url="https://c.gov.cn/z", gap="丙")
    assert r["updated"] is False and r.get("appended") is True
    text = case_file.read_text(encoding="utf-8")
    assert "#99" in text and "标题重复" in text and "https://c.gov.cn/z" in text


def test_update_case_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_docs, "path_of", lambda name: tmp_path / "missing.md")
    r = agent_docs.update_case(1, "已入库")
    assert r["updated"] is False
