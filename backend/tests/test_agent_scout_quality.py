"""04 v1.1：采集质量闸门单测（相关性 / 条款证据 / 知识含量 / INSUFFICIENT）。"""
from __future__ import annotations

from src.ai import agent_scout as S


# ---------------------------------------------------------------- 关键词与相关性
def test_keywords_drop_stopwords():
    kws = S._keywords("涉及水下作业的场景时人员需要配备哪些装备")
    assert "水下" in kws or "水下作业" in kws
    assert "哪些" not in kws and "需要" not in kws


def test_irrelevant_gov_page_rejected_even_if_authority():
    text = "电信业务经营许可企业信息公示：统一社会信用代码 91110108，注册资本 6800 万元，投诉回复率 100"
    ok, why = S._is_relevant("https://dxzhgl.miit.gov.cn/x.jsp", text, ["水下作业", "装备", "潜水"])
    assert ok is False and "关键词" in why


def test_relevant_gov_page_accepted_with_one_hit():
    text = "潜水作业安全规程：潜水员应佩戴潜水头盔，水下作业前检查供气系统。"
    ok, _why = S._is_relevant("https://www.mem.gov.cn/a.html", text, ["水下作业", "潜水", "装备"])
    assert ok is True


def test_non_authority_needs_clause_evidence():
    generic = "水下作业很危险，潜水员要注意安全，装备要齐全，作业前要检查。" * 3
    ok, why = S._is_relevant("https://blog.example.com/p", generic, ["水下作业", "潜水"])
    assert ok is False and "条款" in why

    with_clause = generic + "《潜水员管理办法》第十二条规定：下水前应检查气瓶压力。"
    ok2, _ = S._is_relevant("https://blog.example.com/p2", with_clause, ["水下作业", "潜水"])
    assert ok2 is True


def test_soft_deny_host_always_rejected():
    ok, why = S._is_relevant("https://baike.baidu.com/item/x", "水下作业 潜水 装备 第12条", ["水下作业"])
    assert ok is False and "黑名单" in why


# ---------------------------------------------------------------- 知识含量闸门
def _good_md() -> str:
    """合格样例：正文纯文字、零链接，只在末尾「来源」留 1 条 URL。"""
    lines = [
        "# 井下爆破个体防护要求",
        "",
        "> Agent 贡献（m） · 来源 1 篇 · 抓取 2026-10-26 · 待人工审核",
        "",
        "## 关键要求",
    ]
    for i in range(8):
        lines.append(
            f"{i + 1}. 爆破作业人员进入作业面前应佩戴防尘口罩并检查气密性，"
            f"发现破损或呼吸阻力异常时立即更换，不得带病佩戴。"
        )
    lines.append("## 依据")
    lines.append("《金属非金属矿山安全规程》第 6.3.2 条。")
    lines.append("## 来源")
    lines.append("- https://www.mem.gov.cn/a.shtml")
    return "\n".join(lines)


def test_quality_gate_accepts_real_entry():
    md = _good_md()
    ok, why = S._knowledge_quality(md, 300)
    assert ok is True, why


def test_quality_gate_rejects_gap_narrative():
    md = "# 缺口说明\n\n## 适用范围\n待补录\n\n本次结论：未取得可用内容\n"
    ok, why = S._knowledge_quality(md, 10)
    assert ok is False and ("缺口" in why or "待补录" in why)


def test_quality_gate_rejects_insufficient_sentinel():
    ok, why = S._knowledge_quality("INSUFFICIENT", 10)
    assert ok is False and "不足以成文" in why


def test_quality_gate_rejects_inline_links():
    """正文贴链接 = 索引文档，必须拒。"""
    md = _good_md().replace(
        "1. 爆破作业人员进入作业面前应佩戴防尘口罩并检查气密性，发现破损或呼吸阻力异常时立即更换，不得带病佩戴。",
        "1. 佩戴防尘口罩 [来源: https://www.mem.gov.cn/a.shtml]",
    )
    ok, why = S._knowledge_quality(md, 10)
    assert ok is False and "索引文档" in why


def test_quality_gate_rejects_too_many_source_urls():
    md = _good_md() + "\n" + "\n".join(f"- https://www.mem.gov.cn/{i}.shtml" for i in range(6))
    ok, why = S._knowledge_quality(md, 10)
    assert ok is False and ("3" in why or "占比" in why)


def test_strip_links_removes_inline_and_caps_sources():
    dirty = _good_md().replace(
        "1. 爆破作业人员进入作业面前应佩戴防尘口罩并检查气密性，发现破损或呼吸阻力异常时立即更换，不得带病佩戴。",
        "1. 佩戴防尘口罩。[来源: https://www.mem.gov.cn/a.shtml]",
    ) + "\n- https://www.mem.gov.cn/b.shtml\n- https://www.mem.gov.cn/c.shtml\n- https://www.mem.gov.cn/d.shtml\n"
    clean = S._strip_links(dirty)
    body_urls, ratio = S._link_stats(clean)
    assert body_urls == 0 and ratio == 0
    assert clean.count("## 来源") == 1
    assert len([ln for ln in clean.splitlines() if ln.startswith("- http")]) <= 3
