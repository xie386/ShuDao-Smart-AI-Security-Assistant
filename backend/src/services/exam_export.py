"""考试工坊 · 试卷导出（md / docx / pdf 三种格式）。

输入：出卷子智能体返回的试卷 dict（{"title", "total_score", "questions": [...]}）；
输出：按格式渲染文件落盘，返回相对路径（uploads/exam/{paper_id}.{ext}）。

依赖（全局环境已装）：python-docx 1.2.0 / reportlab 4.4.10。
"""
import logging
import uuid
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)

# 题型 → 中文名（导出排版用）
TYPE_NAMES = {
    "single_choice": "单选题",
    "multi_choice": "多选题",
    "true_false": "判断题",
    "essay": "简答题",
}

# 导出文件目录（相对 backend/），main.py 挂载为 /exam-files 静态托管
EXAM_FILES_DIR = Path(settings.UPLOAD_DIR) / "exam"


def _group_by_type(questions: list[dict]) -> list[tuple[str, list[dict]]]:
    """按题型分组，保持出现顺序。"""
    ordered: list[tuple[str, list[dict]]] = []
    index: dict[str, int] = {}
    for q in questions:
        qtype = q.get("type", "essay")
        if qtype not in index:
            index[qtype] = len(ordered)
            ordered.append((qtype, []))
        ordered[index[qtype]][1].append(q)
    return ordered


def _option_lines(q: dict) -> list[str]:
    """客观题选项行（A. xxx）。"""
    return [f"{chr(65 + i)}. {opt}" for i, opt in enumerate(q.get("options") or [])]


def _answer_line(q: dict) -> str:
    """答案行（判断题显示 正确/错误；简答显示参考答案）。"""
    return f"【答案】{q.get('answer', '')}"


# ---------------- MD ----------------

def to_md(paper: dict) -> str:
    """渲染 Markdown 文本。"""
    lines = [
        f"# {paper.get('title', '安全知识测试')}",
        "",
        f"总分：{paper.get('total_score', 100)} 分",
        "",
    ]
    no = 0
    for qtype, questions in _group_by_type(paper.get("questions", [])):
        lines.append(f"## {TYPE_NAMES.get(qtype, qtype)}")
        lines.append("")
        for q in questions:
            no += 1
            lines.append(f"**{no}. {q.get('content', '')}**（{q.get('score', 0)}分）")
            lines.append("")
            if qtype in ("single_choice", "multi_choice"):
                lines.extend(_option_lines(q))
                lines.append("")
            lines.append(f"- {_answer_line(q)}")
            explanation = q.get("explanation")
            if explanation:
                lines.append(f"- 【解析】{explanation}")
            lines.append("")
    return "\n".join(lines)


# ---------------- DOCX ----------------

def to_docx(paper: dict, path: str | Path) -> Path:
    """渲染 Word 文档（python-docx）。"""
    from docx import Document  # lazy
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(paper.get("title", "安全知识测试"), level=0)
    p = doc.add_paragraph(f"总分：{paper.get('total_score', 100)} 分")
    p.runs[0].font.size = Pt(11)

    no = 0
    for qtype, questions in _group_by_type(paper.get("questions", [])):
        doc.add_heading(TYPE_NAMES.get(qtype, qtype), level=1)
        for q in questions:
            no += 1
            para = doc.add_paragraph()
            para.add_run(f"{no}. {q.get('content', '')}（{q.get('score', 0)}分）").bold = True
            if qtype in ("single_choice", "multi_choice"):
                for opt in _option_lines(q):
                    doc.add_paragraph(opt)
            ans = doc.add_paragraph()
            ans.add_run(_answer_line(q)).bold = True
            explanation = q.get("explanation")
            if explanation:
                doc.add_paragraph(f"【解析】{explanation}")
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dest)
    return dest


# ---------------- PDF ----------------

def to_pdf(paper: dict, path: str | Path) -> Path:
    """渲染 PDF（reportlab platypus 流式排版 + STSong-Light 中文字体）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    title_style = ParagraphStyle("title", fontName="STSong-Light", fontSize=18, leading=24, alignment=1)
    head_style = ParagraphStyle("head", fontName="STSong-Light", fontSize=13, leading=18, spaceBefore=8)
    body_style = ParagraphStyle("body", fontName="STSong-Light", fontSize=10.5, leading=16)
    ans_style = ParagraphStyle("ans", fontName="STSong-Light", fontSize=10.5, leading=16, textColor="#7B241C")

    story = [
        Paragraph(paper.get("title", "安全知识测试"), title_style),
        Spacer(1, 4 * mm),
        Paragraph(f"总分：{paper.get('total_score', 100)} 分", body_style),
        Spacer(1, 4 * mm),
    ]

    no = 0
    for qtype, questions in _group_by_type(paper.get("questions", [])):
        story.append(Paragraph(TYPE_NAMES.get(qtype, qtype), head_style))
        for q in questions:
            no += 1
            story.append(Paragraph(f"{no}. {q.get('content', '')}（{q.get('score', 0)}分）", body_style))
            if qtype in ("single_choice", "multi_choice"):
                for opt in _option_lines(q):
                    story.append(Paragraph(opt, body_style))
            story.append(Paragraph(_answer_line(q), ans_style))
            explanation = q.get("explanation")
            if explanation:
                story.append(Paragraph(f"【解析】{explanation}", body_style))
            story.append(Spacer(1, 2 * mm))

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(dest), pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm).build(story)
    return dest


# ---------------- 统一入口 ----------------

def export_paper(paper: dict, fmt: str, paper_id: str) -> str:
    """按格式导出试卷文件，返回相对路径（如 exam/paper_xxx.md）。

    fmt: md / docx / pdf（非法抛 ValueError）
    """
    fmt = (fmt or "md").lower()
    if fmt not in ("md", "docx", "pdf"):
        raise ValueError(f"不支持的导出格式: {fmt}（可选 md/docx/pdf）")

    renderer = {"md": to_md, "docx": to_docx, "pdf": to_pdf}[fmt]
    if fmt == "md":
        # md 直接写文本文件
        EXAM_FILES_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{paper_id}.md"
        (EXAM_FILES_DIR / filename).write_text(renderer(paper), encoding="utf-8")
    else:
        filename = f"{paper_id}.{fmt}"
        renderer(paper, EXAM_FILES_DIR / filename)

    rel = f"exam/{filename}"
    logger.info("试卷已导出: %s（%s）", rel, fmt)
    return rel
