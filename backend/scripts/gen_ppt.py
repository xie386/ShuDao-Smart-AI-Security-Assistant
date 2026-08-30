#!/usr/bin/env python
"""AI 生成 PPT 流水线演示（P01 大纲生成 + A/B 混合渲染）。

流程：
1. render("ppt_outline", topic) 取 prompts.yml 的大纲生成 prompt；
2. llm_client.chat 调 DeepSeek 生成大纲 JSON（title/subtitle/sections）；
3. B 方案渲染封面（HTML/CSS 渐变标题页 → playwright 截图嵌入）；
4. A 方案渲染目录 + 各章节内容页 + 结尾（python-pptx 商务模板版式）；
5. 输出 .pptx。

用法（在 backend/ 目录下，全局 Python）：
    PYTHONPATH= C:\\python\\python.exe scripts/gen_ppt.py "施工现场高处作业安全"
    PYTHONPATH= C:\\python\\python.exe scripts/gen_ppt.py              # 默认主题

需要 .env 配置 DEEPSEEK_API_KEY（当前已配置）。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pptx import Presentation  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR  # noqa: E402
from pptx.util import Inches, Pt  # noqa: E402

from src.ai.llm_client import llm_client  # noqa: E402
from src.ai.prompts import render  # noqa: E402

# ---------- 常量 ----------
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "ppt_demo"
ASSETS = OUT_DIR / "assets"
NAVY = RGBColor(0x1F, 0x38, 0x64)
GOLD = RGBColor(0xC9, 0xA2, 0x27)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GRAY = RGBColor(0x59, 0x59, 0x59)
FONT = "微软雅黑"
SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)
DEFAULT_TOPIC = "施工现场高处作业安全"


# ---------- 1. 大纲生成（P01：prompts.yml 的 ppt_outline 场景） ----------
def generate_outline(topic: str) -> dict:
    """调 LLM 生成 PPT 大纲 JSON（宽容解析：剥离 ```json 围栏/杂质）。"""
    system_prompt = render("ppt_outline", topic=topic)
    content = llm_client.chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请为主题《{topic}》生成大纲。"},
        ],
        temperature=0.3,
    )
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"LLM 未返回 JSON 大纲: {content[:200]}")
    return json.loads(text[start : end + 1])


# ---------- A 方案：python-pptx 商务模板版式 ----------
def add_bg(slide, color):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color


def add_rect(slide, x, y, w, h, color):
    from pptx.enum.shapes import MSO_SHAPE

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def add_text(slide, x, y, w, h, text, size=18, color=NAVY, bold=False,
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


def header(slide, title):
    add_rect(slide, 0, 0, SLIDE_W, Inches(1.1), NAVY)
    add_rect(slide, 0, Inches(1.1), SLIDE_W, Inches(0.06), GOLD)
    add_text(slide, Inches(0.7), Inches(0.18), Inches(11.5), Inches(0.8),
             title, size=28, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def render_toc(prs, sections):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, WHITE)
    header(slide, "目录 CONTENTS")
    y = 1.8
    for i, sec in enumerate(sections):
        add_rect(slide, Inches(1.5), Inches(y), Inches(0.85), Inches(0.85),
                 NAVY if i % 2 == 0 else GOLD)
        add_text(slide, Inches(1.5), Inches(y), Inches(0.85), Inches(0.85),
                 f"{i + 1:02d}", size=24, color=WHITE, bold=True,
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        add_text(slide, Inches(2.6), Inches(y), Inches(9.5), Inches(0.85),
                 sec.get("name", f"章节 {i + 1}"), size=22, color=NAVY,
                 anchor=MSO_ANCHOR.MIDDLE)
        y += 1.05


def render_content(prs, idx, sec):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, WHITE)
    header(slide, sec.get("name", f"章节 {idx}"))
    add_rect(slide, Inches(0.7), Inches(1.7), Inches(0.08), Inches(4.4), GOLD)
    body = add_text(slide, Inches(1.1), Inches(1.7), Inches(11.2), Inches(5.0),
                    "", size=20)
    tf = body.text_frame
    points = sec.get("points") or []
    for i, pt in enumerate(points[:5]):  # 每页要点 ≤5（任务书 2.8 规范）
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(16)
        run = p.add_run()
        run.text = f"▸ {pt}"
        run.font.name = FONT
        run.font.size = Pt(20)
        run.font.color.rgb = NAVY if i % 2 == 0 else GRAY
    add_text(slide, Inches(12.2), Inches(7.0), Inches(1.0), Inches(0.4),
             str(idx + 3), size=14, color=GRAY, align=PP_ALIGN.RIGHT)


def render_ending(prs, title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, NAVY)
    add_text(slide, Inches(1.2), Inches(2.5), Inches(11), Inches(1.2),
             "高高兴兴上班来，平平安安回家去", size=40, color=WHITE, bold=True,
             align=PP_ALIGN.CENTER)
    add_text(slide, Inches(1.2), Inches(3.9), Inches(11), Inches(0.6),
             f"《{title}》· 感谢聆听", size=20, color=GOLD, align=PP_ALIGN.CENTER)
    add_text(slide, Inches(1.2), Inches(6.4), Inches(11), Inches(0.5),
             "蜀道集团安全管理部 ｜ 2026", size=14, color=WHITE, align=PP_ALIGN.CENTER)


# ---------- B 方案：HTML/CSS 封面 → playwright 截图 ----------
def cover_html(title: str, subtitle: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: "Microsoft YaHei", sans-serif; }}
  .page {{
    width: 1280px; height: 720px; padding: 90px 100px;
    background: linear-gradient(135deg, #0b1a2e 0%, #1f3864 60%, #2c5b8a 100%);
    color: #fff; display: flex; flex-direction: column; justify-content: center;
  }}
  .line {{ width: 90px; height: 6px; background: #c9a227; margin-bottom: 30px; }}
  .title {{ font-size: 60px; font-weight: 800; letter-spacing: 4px; margin-bottom: 20px; }}
  .sub {{ font-size: 26px; color: #c9d6e8; margin-bottom: 70px; }}
  .meta {{ font-size: 18px; color: #8fa5c2; display: flex; justify-content: space-between; }}
</style></head><body>
<div class="page">
  <div class="line"></div>
  <div class="title">{title}</div>
  <div class="sub">{subtitle}</div>
  <div class="meta"><span>蜀道集团安全管理部 · 安全培训课件</span><span>2026年8月</span></div>
</div>
</body></html>"""


def render_cover_b(prs, title: str, subtitle: str):
    """HTML 封面 → playwright（系统 Chrome）截图 → 嵌入。"""
    from playwright.sync_api import sync_playwright

    ASSETS.mkdir(exist_ok=True)
    html_path = ASSETS / "cover_b.html"
    png_path = ASSETS / "cover_b.png"
    html_path.write_text(cover_html(title, subtitle), encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(html_path.as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(png_path))
        browser.close()

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, WHITE)
    slide.shapes.add_picture(str(png_path), 0, 0, SLIDE_W, SLIDE_H)


# ---------- 主流程 ----------
def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TOPIC
    print(f"[1/4] 调 DeepSeek 生成大纲（prompts.yml: ppt_outline）...")
    outline = generate_outline(topic)
    title = outline.get("title") or topic
    subtitle = outline.get("subtitle") or "安全培训讲座课件"
    sections = outline.get("sections") or []
    print(f"      主题: {title} ｜ 章节数: {len(sections)}")
    for s in sections:
        print(f"      - {s.get('name','?')}（{len(s.get('points') or [])} 要点）")

    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    print("[2/4] B 方案渲染封面（HTML→截图）...")
    render_cover_b(prs, title, subtitle)

    print("[3/4] A 方案渲染目录/内容/结尾（python-pptx）...")
    render_toc(prs, sections)
    # 跳过 LLM 偶尔把"目录"也生成进 sections 的冗余章节
    content_secs = [s for s in sections if "目录" not in s.get("name", "")]
    for i, sec in enumerate(content_secs, start=1):
        render_content(prs, i, sec)
    render_ending(prs, title)

    OUT_DIR.mkdir(exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)
    out = OUT_DIR / f"AI生成_{safe}.pptx"
    prs.save(out)
    print(f"[4/4] 完成: {out}（共 {len(prs.slides._sldIdLst)} 页）")


if __name__ == "__main__":
    main()
