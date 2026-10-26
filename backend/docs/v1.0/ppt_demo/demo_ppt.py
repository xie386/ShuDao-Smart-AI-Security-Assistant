#!/usr/bin/env python
"""PPT 生成 A+B 方案演示脚本（为 D4 PPT 模块技术选型提供实证）。

主题：《施工现场高处作业安全》安全讲座（贴合蜀道项目方向 A 场景）。

- A 方案：python-pptx 手绘商务模板（深蓝+金配色，封面/目录/内容/结尾 4 页）
  → 证明"模板管设计、AI 管内容"的路线的效果下限（纯离线、零依赖）。
- B 方案：HTML/CSS 设计数据页（渐变背景+统计卡片+进度条）→ playwright 截图为高清图嵌入
  → 证明 HTML 路线的效果上限（设计自由度拉满）。

产出（docs/ppt_demo/）：
    演示_高处作业安全_AB方案.pptx   最终成品（A 页 + B 页合并）
    assets/page_b_data.html         B 方案 HTML 源
    assets/page_b_data.png          B 方案截图（1280x720）

用法（在 backend/ 目录下，全局 Python）：
    PYTHONPATH= C:\\python\\python.exe docs/ppt_demo/demo_ppt.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

# ---------- 常量 ----------
OUT_DIR = Path(__file__).resolve().parent
ASSETS = OUT_DIR / "assets"
OUT_PPTX = OUT_DIR / "演示_高处作业安全_AB方案.pptx"

NAVY = RGBColor(0x1F, 0x38, 0x64)      # 深蓝主色
GOLD = RGBColor(0xC9, 0xA2, 0x27)      # 金色点缀
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GRAY = RGBColor(0x59, 0x59, 0x59)
FONT = "微软雅黑"

SLIDE_W, SLIDE_H = Inches(13.333), Inches(7.5)


# ---------- 通用工具 ----------
def add_bg(slide, color):
    """整页背景色。"""
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = color


def add_rect(slide, x, y, w, h, color, line=None):
    from pptx.enum.shapes import MSO_SHAPE

    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if line:
        shape.line.color.rgb = line
    else:
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


def content_page(prs, title, bullets, page_no):
    """A 方案内容页：深蓝标题栏 + 金色竖条 + 要点 + 页脚页码。"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    add_bg(slide, WHITE)
    add_rect(slide, 0, 0, SLIDE_W, Inches(1.1), NAVY)                 # 标题栏
    add_rect(slide, 0, Inches(1.1), SLIDE_W, Inches(0.06), GOLD)      # 金色分隔线
    add_text(slide, Inches(0.7), Inches(0.18), Inches(11), Inches(0.8),
             title, size=30, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    add_rect(slide, Inches(0.7), Inches(1.7), Inches(0.08), Inches(3.6), GOLD)  # 左侧竖条
    body = add_text(slide, Inches(1.1), Inches(1.7), Inches(11.2), Inches(4.6),
                    "", size=20, color=GRAY)
    tf = body.text_frame
    for i, b in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(14)
        run = p.add_run()
        run.text = f"▸ {b}"
        run.font.name = FONT
        run.font.size = Pt(20)
        run.font.color.rgb = NAVY if i % 2 == 0 else GRAY
    add_text(slide, Inches(12.3), Inches(7.0), Inches(0.9), Inches(0.4),
             str(page_no), size=14, color=GRAY, align=PP_ALIGN.RIGHT)


# ---------- A 方案：python-pptx 商务模板 ----------
def build_a(prs):
    # 封面
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, NAVY)
    add_rect(slide, 0, Inches(6.9), SLIDE_W, Inches(0.6), GOLD)       # 底部金条
    add_text(slide, Inches(1.2), Inches(2.2), Inches(11), Inches(1.4),
             "施工现场高处作业安全", size=48, color=WHITE, bold=True)
    add_text(slide, Inches(1.2), Inches(3.6), Inches(11), Inches(0.6),
             "—— 安全讲座 · 课件 ——", size=22, color=GOLD)
    add_text(slide, Inches(1.2), Inches(6.2), Inches(8), Inches(0.5),
             "蜀道集团安全管理部 ｜ 2026年8月", size=16, color=WHITE)

    # 目录
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, WHITE)
    add_rect(slide, 0, 0, SLIDE_W, Inches(1.1), NAVY)
    add_rect(slide, 0, Inches(1.1), SLIDE_W, Inches(0.06), GOLD)
    add_text(slide, Inches(0.7), Inches(0.18), Inches(6), Inches(0.8),
             "目录 CONTENTS", size=30, color=WHITE, bold=True,
             anchor=MSO_ANCHOR.MIDDLE)
    toc = ["01  高处作业的定义与判定标准", "02  高处坠落的主要风险点",
           "03  安全防护的硬性要求", "04  典型事故案例警示"]
    y = 1.9
    for i, t in enumerate(toc):
        add_rect(slide, Inches(1.5), Inches(y), Inches(0.9), Inches(0.9),
                 NAVY if i % 2 == 0 else GOLD)
        add_text(slide, Inches(1.5), Inches(y), Inches(0.9), Inches(0.9),
                 str(i + 1), size=26, color=WHITE, bold=True,
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        add_text(slide, Inches(2.7), Inches(y), Inches(9), Inches(0.9),
                 t, size=22, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
        y += 1.2

    # 内容页 ×2
    content_page(prs, "一、高处作业的定义",
                 ["凡在坠落高度基准面 2m 及以上、有可能坠落的高处进行的作业",
                  "常见场景：脚手架搭拆、临边作业、洞口作业、屋面作业、吊装作业",
                  "作业前须确认：作业票、防护设施、人员状态三项达标",
                  "分级管理：一般高处作业 / 特殊高处作业（恶劣天气、夜间等）"], 3)
    content_page(prs, "二、安全防护的硬性要求（要点）",
                 ["安全带：高挂低用，严禁低挂高用，做到一人一带",
                  "脚手架：作业层满铺脚手板，设 1.2m 防护栏杆与挡脚板",
                  "临边/洞口：设置防护栏杆、盖板或安全网，挂警示标志",
                  "交叉作业：设置隔离防护棚，动火作业清理下方可燃物",
                  "安全帽：进入现场全程佩戴，系好下颚带"], 4)

    # 结尾页
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, NAVY)
    add_text(slide, Inches(1.2), Inches(2.6), Inches(11), Inches(1.2),
             "高高兴兴上班来，平平安安回家去", size=40, color=WHITE, bold=True,
             align=PP_ALIGN.CENTER)
    add_text(slide, Inches(1.2), Inches(4.0), Inches(11), Inches(0.6),
             "感谢聆听 · 愿每一次高处作业都安全归来", size=20, color=GOLD,
             align=PP_ALIGN.CENTER)
    add_text(slide, Inches(1.2), Inches(6.4), Inches(11), Inches(0.5),
             "蜀道集团安全管理部 ｜ 2026", size=14, color=WHITE,
             align=PP_ALIGN.CENTER)


# ---------- B 方案：HTML/CSS → 截图 → 嵌入 ----------
HTML_B = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: "Microsoft YaHei", sans-serif; }
  .page {
    width: 1280px; height: 720px; padding: 56px 72px;
    background: linear-gradient(135deg, #0f2027 0%, #203a43 55%, #2c5364 100%);
    color: #fff; display: flex; flex-direction: column;
  }
  .tag { font-size: 22px; color: #7fd4c1; letter-spacing: 6px; margin-bottom: 12px; }
  .title { font-size: 46px; font-weight: 700; margin-bottom: 6px; }
  .sub { font-size: 18px; color: #9fb3c8; margin-bottom: 40px; }
  .cards { display: flex; gap: 32px; margin-bottom: 44px; }
  .card {
    flex: 1; background: rgba(255,255,255,.08); border-radius: 14px;
    padding: 30px 24px; border: 1px solid rgba(255,255,255,.12);
  }
  .num { font-size: 54px; font-weight: 800; color: #7fd4c1; margin-bottom: 10px; }
  .label { font-size: 17px; color: #d7e3ef; line-height: 1.5; }
  .bar-wrap { background: rgba(255,255,255,.1); border-radius: 8px; height: 34px;
              padding: 4px; }
  .bar { width: 95%; height: 26px; border-radius: 6px;
         background: linear-gradient(90deg, #11998e, #38ef7d);
         display: flex; align-items: center; justify-content: flex-end;
         padding-right: 10px; font-size: 15px; font-weight: 700; color: #04222a; }
  .foot { margin-top: auto; font-size: 15px; color: #7d8ea3;
          display: flex; justify-content: space-between; }
</style></head><body>
<div class="page">
  <div class="tag">DATA · 2026</div>
  <div class="title">高处坠落 · 数据警示</div>
  <div class="sub">一组数字，说明为什么防护设施不容打折</div>
  <div class="cards">
    <div class="card"><div class="num">42%</div><div class="label">高处坠落占建筑业<br>生产安全事故比例</div></div>
    <div class="card"><div class="num">78%</div><div class="label">正确系挂安全带可<br>避免的坠落伤亡</div></div>
    <div class="card"><div class="num">≥2m</div><div class="label">坠落高度基准面<br>（GB/T 3608-2008）</div></div>
  </div>
  <div class="bar-wrap"><div class="bar">防护装备配置率 95%</div></div>
  <div class="foot"><span>蜀道集团安全管理部 · 安全数据看板</span><span>数据来源：住建部事故统计</span></div>
</div>
</body></html>"""


def build_b(prs):
    """HTML → playwright 截图（系统 Chrome）→ 嵌入 pptx。"""
    from playwright.sync_api import sync_playwright

    ASSETS.mkdir(exist_ok=True)
    html_path = ASSETS / "page_b_data.html"
    png_path = ASSETS / "page_b_data.png"
    html_path.write_text(HTML_B, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto(html_path.as_uri())
        page.wait_for_timeout(400)  # 等字体/渐变渲染
        page.screenshot(path=str(png_path))
        browser.close()

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, WHITE)
    slide.shapes.add_picture(str(png_path), 0, 0, SLIDE_W, SLIDE_H)


# ---------- 主流程 ----------
def main() -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    build_a(prs)   # A 方案 6 页（封面/目录/2内容/结尾 = 5 页）
    build_b(prs)   # B 方案 1 页（HTML 数据页）
    OUT_DIR.mkdir(exist_ok=True)
    prs.save(OUT_PPTX)
    print(f"已生成: {OUT_PPTX}")
    print(f"总页数: {len(prs.slides.__iter__.__self__._sldIdLst)}  "
          f"（A 方案 5 页 + B 方案 1 页）")
    print(f"B 方案产物: {ASSETS / 'page_b_data.html'} / {ASSETS / 'page_b_data.png'}")


if __name__ == "__main__":
    main()
