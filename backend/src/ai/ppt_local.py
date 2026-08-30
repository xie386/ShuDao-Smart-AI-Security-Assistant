"""PPT 本地生成兜底方案（D4：百度 API 免费额度用尽/故障时降级使用）。

提炼自 scripts/gen_ppt.py（D3 已验证的 A+B 混合方案）：
- A 方案：python-pptx 商务模板（深蓝+金）渲染 封面/目录/内容/结尾；
- B 方案：HTML/CSS 封面 → playwright 截图嵌入（本机 Chrome 可用）。
此处提供纯 A 方案（离线零依赖，最稳），B 方案封面可选启用。

对外接口（供 ppt_service 调用）：
    generate_local_ppt(outline: dict, topic: str, out_path: Path) -> int  # 返回页数
"""
import logging
import re
from pathlib import Path

from pptx import Presentation  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

logger = logging.getLogger(__name__)

NAVY = RGBColor(0x1F, 0x38, 0x64)
GOLD = RGBColor(0xC9, 0xA2, 0x27)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GRAY = RGBColor(0x59, 0x59, 0x59)
FONT = "微软雅黑"
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)


def _add_bg(slide, color):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color


def _add_rect(slide, x, y, w, h, color):
    from pptx.enum.shapes import MSO_SHAPE

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _add_text(slide, x, y, w, h, text, size=18, color=NAVY, bold=False,
              align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def _header(slide, title):
    _add_rect(slide, 0, 0, SLIDE_W, Inches(1.1), NAVY)
    _add_rect(slide, 0, Inches(1.1), SLIDE_W, Inches(0.06), GOLD)
    _add_text(slide, Inches(0.7), Inches(0.18), Inches(11.5), Inches(0.8),
              title, size=28, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def _render_cover(prs, title, subtitle):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_bg(slide, NAVY)
    _add_rect(slide, 0, Inches(6.9), SLIDE_W, Inches(0.6), GOLD)
    _add_text(slide, Inches(1.2), Inches(2.2), Inches(11), Inches(1.4),
              title, size=48, color=WHITE, bold=True)
    _add_text(slide, Inches(1.2), Inches(3.6), Inches(11), Inches(0.6),
              f"—— {subtitle} ——", size=22, color=GOLD)
    _add_text(slide, Inches(1.2), Inches(6.2), Inches(8), Inches(0.5),
              "蜀道集团安全管理部 ｜ 安全培训课件", size=16, color=WHITE)


def _render_toc(prs, sections):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_bg(slide, WHITE)
    _header(slide, "目录 CONTENTS")
    y = 1.8
    for i, sec in enumerate(sections):
        _add_rect(slide, Inches(1.5), Inches(y), Inches(0.85), Inches(0.85),
                  NAVY if i % 2 == 0 else GOLD)
        _add_text(slide, Inches(1.5), Inches(y), Inches(0.85), Inches(0.85),
                  f"{i + 1:02d}", size=24, color=WHITE, bold=True,
                  align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        _add_text(slide, Inches(2.6), Inches(y), Inches(9.5), Inches(0.85),
                  sec.get("name", f"章节 {i + 1}"), size=22, color=NAVY,
                  anchor=MSO_ANCHOR.MIDDLE)
        y += 1.05


def _render_content(prs, idx, sec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_bg(slide, WHITE)
    _header(slide, sec.get("name", f"章节 {idx}"))
    _add_rect(slide, Inches(0.7), Inches(1.7), Inches(0.08), Inches(4.4), GOLD)
    body = _add_text(slide, Inches(1.1), Inches(1.7), Inches(11.2), Inches(5.0),
                     "", size=20)
    tf = body.text_frame
    points = sec.get("points") or []
    for i, pt in enumerate(points[:5]):  # 每页要点 ≤5（任务书 2.8）
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(16)
        run = p.add_run()
        run.text = f"▸ {pt}"
        run.font.name = FONT
        run.font.size = Pt(20)
        run.font.color.rgb = NAVY if i % 2 == 0 else GRAY
    _add_text(slide, Inches(12.2), Inches(7.0), Inches(1.0), Inches(0.4),
              str(idx + 3), size=14, color=GRAY, align=PP_ALIGN.RIGHT)


def _render_ending(prs, title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _add_bg(slide, NAVY)
    _add_text(slide, Inches(1.2), Inches(2.5), Inches(11), Inches(1.2),
              "高高兴兴上班来，平平安安回家去", size=40, color=WHITE, bold=True,
              align=PP_ALIGN.CENTER)
    _add_text(slide, Inches(1.2), Inches(3.9), Inches(11), Inches(0.6),
              f"《{title}》· 感谢聆听", size=20, color=GOLD, align=PP_ALIGN.CENTER)
    _add_text(slide, Inches(1.2), Inches(6.4), Inches(11), Inches(0.5),
              "蜀道集团安全管理部 ｜ 安全培训课件", size=14, color=WHITE,
              align=PP_ALIGN.CENTER)


def generate_local_ppt(
    outline: dict, topic: str, out_path: Path, template_path: Path | None = None
) -> int:
    """用 python-pptx 商务模板渲染大纲 → 保存 pptx，返回页数。

    outline 结构：{"title", "subtitle", "sections": [{"name", "points"}]}
    与百度方案的 outline JSON 结构一致（schemas/ppt.py 契约）。

    template_path（领导优化）：用户上传的自定义模板 .pptx 时，以其为基底
    （保留模板首页/母版/样式），在末尾追加 封面/目录/内容/结尾 页。
    """
    title = (outline.get("title") or topic).strip()
    subtitle = (outline.get("subtitle") or "安全培训讲座课件").strip()
    sections = outline.get("sections") or []

    if template_path is not None and template_path.exists():
        # 用户模板基底：保留既有页与母版，追加内容
        prs = Presentation(str(template_path))
        # 若模板本身没有 16:9 设置，统一为 16:9（避免默认 4:3 与内容页冲突）
        prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    else:
        prs = Presentation()
        prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    _render_cover(prs, title, subtitle)
    _render_toc(prs, sections)
    content_secs = [s for s in sections if "目录" not in (s.get("name") or "")]
    for i, sec in enumerate(content_secs, start=1):
        _render_content(prs, i, sec)
    _render_ending(prs, title)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out_path)
    page_count = len(prs.slides._sldIdLst)
    logger.info("本地 PPT 生成完成: %s（%d 页，模板=%s）", out_path, page_count, template_path or "内置商务")
    return page_count
