"""入库清链接：单元测试。"""
from __future__ import annotations

from src.services.kb_govern import clean_for_index


def test_drops_reference_section_and_urls():
    md = """# 标题

## 关键要求
1. 入井人员应随身携带自救器，防护时间不少于 30 分钟。
2. 每班组配置一台便携式气体检测报警仪。

## 依据
- GB 20826-2021《潜水员高压水射流作业安全规程》

## 来源
- https://std.samr.gov.cn/gb/search/gbDetailed?id=71F7
- http://jsj.zs.gov.cn/zwgk/zcwj/qtwj/content/post_2016378.html
"""
    out = clean_for_index(md)
    assert "http" not in out
    assert "来源" not in out
    assert "入井人员应随身携带自救器" in out
    assert "GB 20826-2021" in out          # 依据（无 URL）保留，是知识的一部分


def test_removes_inline_citations():
    md = "1. 必须配齐自救器 [来源: https://www.mem.gov.cn/a.shtml]。\n2. 爆破后通风（出处：https://x.gov.cn/b）。"
    out = clean_for_index(md)
    assert "http" not in out and "来源" not in out
    assert "必须配齐自救器" in out


def test_removes_orphan_bullets_and_numbering():
    md = "## 关键要求\n- https://a.com/x\n- \n2. \n3. 正常条目内容，保留。"
    out = clean_for_index(md)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert not any(ln.strip() in {"-", "2."} for ln in lines)
    assert any("正常条目内容" in ln for ln in lines)


def test_keeps_plain_prose_untouched():
    md = "# 标题\n\n## 适用范围\n适用于金属非金属地下矿山企业。\n\n## 关键要求\n1. 每个作业班组配置至少一台便携式气体检测报警仪。"
    assert clean_for_index(md) == md


def test_empty_and_whitespace():
    assert clean_for_index("") == ""
    assert clean_for_index("   \n\n  ") == ""
