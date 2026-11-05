"""06 前端 DOM 验证：用真实 index.html 的 renderHz/applyHzState/hzItemHtml，
不开后端（打桩 api + token），检查框数量、编号标签、聚焦淡化、类别筛选、建议依据渲染。

用法：C:/python/python.exe scratch/verify06_dom.py
输出：JSON 结果 + 三张截图路径
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

FRONT = Path(__file__).resolve().parent.parent.parent / "front"
PORT = 8899
OUT = Path(os.environ.get("LOCALAPPDATA", ".")) / "Temp" / "hz_dom"
OUT.mkdir(parents=True, exist_ok=True)

PALETTE = {
    "category_hues": {"人的不安全行为": 212, "物的不安全状态": 32, "环境的不安全因素": 145, "管理缺陷": 268},
    "default_category": "物的不安全状态",
    "level_lightness": {"重大": 0.38, "较大": 0.46, "一般": 0.55, "轻微": 0.64},
    "saturation": 0.62,
    "colors": {
        "人的不安全行为": {"重大": "#1B4A80", "较大": "#2565AA", "一般": "#3D82C7", "轻微": "#6FA5D6"},
        "物的不安全状态": {"重大": "#9C6424", "较大": "#C98530", "一般": "#DCA66A", "轻微": "#E8C79B"},
        "环境的不安全因素": {"重大": "#1F7A4C", "较大": "#27A165", "一般": "#45BD84", "轻微": "#84D8AF"},
        "管理缺陷": {"重大": "#5A2E9C", "较大": "#7740C4", "一般": "#9A6CDB", "轻微": "#BFA0E8"},
    },
    "level_stroke_base": {"重大": 12, "较大": 9, "一般": 7, "轻微": 6},
    "base_width": 4096,
    "fill_alpha": 0.25,
    "focus_fill_alpha": 0.5,
    "dim_fill_alpha": 0.07,
    "outline_alpha": 1.0,
    "white_outline_width": 2,
    "label_mode_default": "index",
    "font_ratio": 1 / 64,
    "font_min": 18,
    "stroke_min": 2,
}

HAZARDS = [
    {"level": "重大", "category": "物的不安全状态", "description": "基坑临边无防护栏杆",
     "location": [200, 300, 900, 800], "suggestion": "沿基坑临边连续搭设防护栏杆并挂警示标识",
     "refs": [{"kind": "kb", "doc": "shd-proc-check-202405", "chapter": "4.基坑支护"}], "grounded": True},
    {"level": "一般", "category": "人的不安全行为", "description": "作业人员未系安全带",
     "location": [1000, 400, 1400, 900], "suggestion": "高处作业必须系挂安全带并高挂低用",
     "refs": [{"kind": "kb", "doc": "shd-proc-check-202405", "chapter": "6.高处作业"}], "grounded": True},
    {"level": "轻微", "category": "环境的不安全因素", "description": "沟渠边缘无警示",
     "location": [1500, 1200, 2100, 1700], "suggestion": "",
     "refs": [], "grounded": False, "gap_note": "网搜未成（超时）"},
]

PAYLOAD = {
    "image_url": "data:image/svg+xml;base64," + __import__("base64").b64encode(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="1800">'
        b'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        b'<stop offset="0" stop-color="#6b7f5e"/><stop offset="1" stop-color="#3d4a33"/></linearGradient></defs>'
        b'<rect width="2400" height="1800" fill="url(#g)"/></svg>').decode(),
    "image_width": 2400,
    "image_height": 1800,
    "summary": "场地内共 3 处隐患，其中 1 处知识库暂无依据。",
    "hazards": HAZARDS,
}


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1", "--directory", str(FRONT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.2)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{PORT}/safety/index.html", wait_until="load")
            page.wait_for_timeout(600)

            # 打桩：token + api()（只用于 box-style）
            page.evaluate("() => { try { token = 'dom-test'; } catch (e) {} }")
            page.evaluate(
                """(palette) => {
                    window.api = async (p) => ({
                        ok: true, status: 200,
                        json: async () => (String(p).includes('box-style') ? palette : {})
                    });
                    hzStyle = null;
                }""",
                PALETTE,
            )
            page.evaluate("() => loadHzStyle()")
            # 打通登录态：页面初始 #app-view 是 hidden（未登录），只切路由仍不可截图
            page.evaluate("""() => {
                document.getElementById('login-view').style.display = 'none';
                document.getElementById('app-view').classList.remove('hidden');
                if (window.me) { window.me.role = 'admin'; }
                else { me = { id: 1, role: 'admin', username: 'dom' }; }
            }""")
            page.wait_for_timeout(200)
            # 必须先进入上报页（否则 #h-ai-result 所在 page 是 display:none，元素不可截图）
            page.evaluate("() => { location.hash = '#/hazards/new'; }")
            page.wait_for_timeout(400)
            page.wait_for_timeout(200)
            page.evaluate("(payload) => applyAiResult(payload)", PAYLOAD)
            page.wait_for_timeout(300)

            res = {}
            res["boxes"] = page.eval_on_selector_all("#h-ai-svg .hz-g", "els => els.length")
            res["labels"] = page.eval_on_selector_all(
                "#h-ai-svg .hz-g text:not(.hz-full)", "els => els.map(e => e.textContent)")
            res["fill_default"] = page.eval_on_selector_all(
                "#h-ai-svg .hz-fill", "els => els.map(e => e.getAttribute('fill-opacity'))")
            res["stroke_widths"] = page.eval_on_selector_all(
                "#h-ai-svg .hz-g", "els => els.map(g => g.querySelectorAll('rect')[2].getAttribute('stroke-width'))")
            res["colors"] = page.eval_on_selector_all(
                "#h-ai-svg .hz-g", "els => els.map(g => g.querySelectorAll('rect')[2].getAttribute('stroke'))")
            res["chips"] = page.eval_on_selector_all("#h-ai-chips .hz-chip", "els => els.map(e => e.textContent.trim())")
            res["card_ids"] = page.eval_on_selector_all(
                "#h-ai-body .hz-idx", "els => els.map(e => e.textContent.trim())")
            res["card_suggestions"] = page.eval_on_selector_all(
                "#h-ai-body .hz-sug, #h-ai-body .hz-gap", "els => els.map(e => e.textContent.trim())")
            res["svg_viewbox"] = page.eval_on_selector("#h-ai-svg", "e => e.getAttribute('viewBox')")

            page.eval_on_selector("#h-ai-result", "e => e.scrollIntoView({block:'start'})")
            page.wait_for_timeout(150)
            page.locator("#h-ai-result").screenshot(path=str(OUT / "01_all.png"))

            # 聚焦第 2 条
            page.evaluate("() => focusHz(2)")
            page.wait_for_timeout(350)
            res["focus_opacities"] = page.eval_on_selector_all("#h-ai-svg .hz-g", "els => els.map(e => e.style.opacity)")
            res["focus_fill"] = page.eval_on_selector_all(
                "#h-ai-svg .hz-fill", "els => els.map(e => e.getAttribute('fill-opacity'))")
            page.locator("#h-ai-result").screenshot(path=str(OUT / "02_focus.png"))

            # 类别筛选：物的不安全状态
            page.evaluate("() => { hzView.focus = 0; hzView.cat = '物的不安全状态'; renderChips(); applyHzState(); }")
            page.wait_for_timeout(300)
            res["filter_opacities"] = page.eval_on_selector_all("#h-ai-svg .hz-g", "els => els.map(e => e.style.opacity)")
            page.locator("#h-ai-result").screenshot(path=str(OUT / "03_filter.png"))

            res["page_errors"] = errors
            res["screenshots"] = [str(OUT / f) for f in ("01_all.png", "02_focus.png", "03_filter.png")]
            browser.close()

        print(json.dumps(res, ensure_ascii=False, indent=2))
        ok = (
            res["boxes"] == 3
            and res["labels"] == ["#1", "#2", "#3"]
            and all(float(x) == 0.25 for x in res["fill_default"])
            and res["card_ids"] == ["#1", "#2", "#3"]
            and res["focus_opacities"] == ["0.07", "1", "0.07"]
            and res["focus_fill"][1] == "0.5"
            and len(res["chips"]) == 4          # 全部 + 3 个类别
            and any("依据：" in s for s in res["card_suggestions"])
            and any("暂无依据" in s or "网搜未成" in s for s in res["card_suggestions"])
            and not res["page_errors"]
        )
        print("\n结论:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
