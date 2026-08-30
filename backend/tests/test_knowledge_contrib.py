"""知识贡献测试：文件解析 + service 常量 + 预筛 JSON 容错（纯函数，不依赖 LLM/DB/网络）。

覆盖：
- _extract_file_text：md/txt 双编码 / docx / pdf 解析
- 常量校验（扩展名白名单/大小上限/简介长度门槛）
- knowledge_review_agent 的 _extract_json / _validate
- 判定标准文档存在性（管理员双保障入口）
"""
from pathlib import Path

import pytest

from src.ai.knowledge_review_agent import _extract_json, _validate
from src.services.knowledge_contrib_service import (
    _ALLOWED_EXTS,
    _CONTENT_MIN,
    _INTRO_MAX,
    _INTRO_MIN,
    _MAX_FILE_BYTES,
    VALID_DOC_TYPES,
    _extract_file_text,
)


class TestExtractFileText:
    def test_md_utf8(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("# 标题\n高处作业规范", encoding="utf-8")
        assert "高处作业规范" in _extract_file_text(p)

    def test_txt_gbk_fallback(self, tmp_path):
        """GBK 编码文件 utf-8 读失败后应兜底 gbk 成功。"""
        p = tmp_path / "b.txt"
        p.write_bytes("临时用电安全要求".encode("gbk"))
        assert "临时用电" in _extract_file_text(p)

    def test_docx(self, tmp_path):
        from docx import Document

        doc = Document()
        doc.add_paragraph("塔吊安装拆卸安全操作规程")
        doc.add_paragraph("第一章 总则")
        p = tmp_path / "c.docx"
        doc.save(str(p))
        text = _extract_file_text(p)
        assert "塔吊" in text and "总则" in text

    def test_unsupported_ext(self, tmp_path):
        p = tmp_path / "d.xyz"
        p.write_text("x")
        assert _extract_file_text(p) == ""


class TestServiceConsts:
    def test_allowed_exts(self):
        assert {".md", ".txt", ".docx", ".pdf"} <= _ALLOWED_EXTS

    def test_content_min(self):
        assert _CONTENT_MIN >= 10

    def test_intro_range(self):
        assert 0 < _INTRO_MIN <= _INTRO_MAX <= 1000

    def test_max_file_bytes(self):
        assert _MAX_FILE_BYTES == 10 * 1024 * 1024

    def test_valid_doc_types(self):
        assert VALID_DOC_TYPES == {"laws", "procedures", "plans", "cases"}


class TestReviewAgentJson:
    def test_extract_plain(self):
        assert _extract_json('{"in_scope": true, "suggestion": "通过"}')["suggestion"] == "通过"

    def test_extract_markdown_wrapped(self):
        raw = '```json\n{"in_scope": false, "suggestion": "驳回", "reason": "校园行业"}\n```'
        assert _extract_json(raw)["in_scope"] is False

    def test_extract_no_json(self):
        with pytest.raises(ValueError):
            _extract_json("没有 JSON 的输出")

    def test_validate_ok(self):
        assert _validate({"suggestion": "通过", "reason": "命中施工核心词"}) is None

    def test_validate_bad_suggestion(self):
        assert "suggestion 非法" in _validate({"suggestion": "未知", "reason": "x"})

    def test_validate_missing_reason(self):
        assert "reason 为空" in _validate({"suggestion": "复核"})


class TestReviewStandard:
    def test_doc_exists(self):
        """判定标准文档必须存在（管理员审批界面查看入口的第二重保障）。"""
        p = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "施工安全领域文件鉴别判定标准.md"
        )
        assert p.exists(), "判定标准文档缺失"
        assert "施工安全领域" in p.read_text(encoding="utf-8")


class TestGovern:
    """在线清洗治理（kb_govern）：HTML 剥离 / 噪声行 / 重复行 / 质量校验。"""

    def test_strip_html(self):
        from src.services.kb_govern import clean_text

        dirty = "<html><body><p>高处作业规范</p><p>必须系挂安全带</p></body></html>"
        out = clean_text(dirty)
        assert "<" not in out and "高处作业规范" in out

    def test_noise_line_removed(self):
        from src.services.kb_govern import clean_text

        out = clean_text("第一章 总则\n版权所有 © 蜀道集团\n备案号：蜀ICP备xxx")
        assert "版权所有" not in out and "备案号" not in out
        assert "第一章 总则" in out

    def test_adjacent_dup_removed(self):
        from src.services.kb_govern import clean_text

        out = clean_text("第一条 目的\n第一条 目的\n第二条 范围")
        assert out.count("第一条 目的") == 1

    def test_quality_ok(self):
        from src.services.kb_govern import quality_check

        text = "施工安全规范。\n" * 200  # 600+ 字，无污染
        qc = quality_check(text)
        assert qc["ok"] is True and qc["repeat_ratio"] == 0.0

    def test_quality_too_short(self):
        from src.services.kb_govern import quality_check

        qc = quality_check("太短")
        assert qc["ok"] is False
        assert any("碎片阈值" in r for r in qc["reasons"])

    def test_quality_repeat(self):
        from src.services.kb_govern import quality_check

        # 整段复制 5 遍 → 重复比例超限
        qc = quality_check(("同一段重复内容" * 50 + "\n") * 6)
        assert qc["ok"] is False
        assert any("重复段落" in r for r in qc["reasons"])
