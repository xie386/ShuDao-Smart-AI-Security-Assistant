"""考试工坊测试：导出渲染 + 出卷校验（纯函数，不依赖 LLM/DB/网络）。

覆盖：
- exam_export：md 文本内容 / docx / pdf 文件生成
- exam_agent：_extract_json 容错、_validate_question 逐题校验、模板列表
"""
import json
from pathlib import Path

from src.ai.exam_agent import ExamPaperAgent, _extract_json, _validate_paper, _validate_question
from src.services import exam_export

# 样例试卷（模拟出卷子智能体输出）
SAMPLE_PAPER = {
    "title": "安全生产常识测试",
    "total_score": 100,
    "questions": [
        {
            "type": "single_choice",
            "content": "高处作业必须佩戴什么防护用品？",
            "options": ["安全帽", "安全带", "护目镜", "口罩"],
            "answer": "B",
            "explanation": "高处作业必须系挂安全带（GB/T 3608）。",
            "score": 5,
        },
        {
            "type": "true_false",
            "content": "酒后可以进入施工现场。",
            "options": [],
            "answer": "错误",
            "explanation": "酒后严禁进入施工现场。",
            "score": 4,
        },
        {
            "type": "essay",
            "content": "简述事故处理的四不放过原则。",
            "options": [],
            "answer": "事故原因未查清不放过、责任人员未处理不放过、整改措施未落实不放过、有关人员未受教育不放过。",
            "explanation": "评分要点：四条各 2.5 分。",
            "score": 10,
        },
    ],
}


class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_wrapped(self):
        raw = '```json\n{"questions": [{"type": "essay"}]}\n```'
        assert _extract_json(raw)["questions"][0]["type"] == "essay"

    def test_no_json(self):
        try:
            _extract_json("纯文本没有 JSON")
            assert False, "应抛 ValueError"
        except ValueError:
            pass


class TestValidateQuestion:
    def test_valid_single(self):
        assert _validate_question(SAMPLE_PAPER["questions"][0]) is None

    def test_invalid_type(self):
        assert "题型非法" in _validate_question({"type": "填空", "content": "x"})

    def test_missing_content(self):
        assert "题干为空" in _validate_question({"type": "essay", "content": "  "})

    def test_too_few_options(self):
        assert "选项" in _validate_question(
            {"type": "single_choice", "content": "x", "options": ["A"], "answer": "A"}
        )

    def test_answer_out_of_range(self):
        assert "答案" in _validate_question(
            {"type": "single_choice", "content": "x", "options": ["A", "B"], "answer": "C"}
        )

    def test_true_false_answer(self):
        assert "正确/错误" in _validate_question({"type": "true_false", "content": "x", "answer": "对"})


class TestValidatePaper:
    def test_valid_paper(self):
        assert _validate_paper(SAMPLE_PAPER) is None

    def test_empty_questions(self):
        assert "questions 为空" in _validate_paper({"questions": []})

    def test_bad_question_indexed(self):
        paper = json.loads(json.dumps(SAMPLE_PAPER))
        paper["questions"][1]["answer"] = "对"
        assert "第 2 题" in _validate_paper(paper)


class TestExport:
    def _tmp_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    def test_to_md(self):
        md = exam_export.to_md(SAMPLE_PAPER)
        assert "# 安全生产常识测试" in md
        assert "单选题" in md and "多选题" not in md
        assert "B. 安全带" in md
        assert "【答案】错误" in md
        assert "总分：100 分" in md

    def test_to_docx(self, tmp_path):
        dest = exam_export.to_docx(SAMPLE_PAPER, tmp_path / "p.docx")
        assert dest.exists() and dest.stat().st_size > 0

    def test_to_pdf(self, tmp_path):
        dest = exam_export.to_pdf(SAMPLE_PAPER, tmp_path / "p.pdf")
        assert dest.exists() and dest.stat().st_size > 0
        assert dest.read_bytes()[:4] == b"%PDF"  # PDF 魔数

    def test_export_unknown_format(self):
        try:
            exam_export.export_paper(SAMPLE_PAPER, "xlsx", "paper_x")
            assert False, "应抛 ValueError"
        except ValueError:
            pass


class TestTemplates:
    def test_list_templates(self):
        tpls = ExamPaperAgent().list_templates()
        keys = {t["key"] for t in tpls}
        assert keys == {"general", "law", "case"}
        assert all(t["total_score"] == 100 for t in tpls)
