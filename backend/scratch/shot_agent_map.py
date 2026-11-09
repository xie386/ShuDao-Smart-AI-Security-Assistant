"""截图验证：设计图-Agent工作与工具调用体系.html（全图 + 4 条链路 + 节点点击）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HTML = ROOT / "设计图-Agent工作与工具调用体系.html"
OUT = Path(os.environ.get("LOCALAPPDATA", ".")) / "Temp" / "agent_map"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1560, "height": 1200}, device_scale_factor=1)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(HTML.as_uri(), wait_until="load")
        page.wait_for_timeout(700)

        info = {}
        info["nodes"] = page.eval_on_selector_all("#map .node", "els => els.length")
        info["edges"] = page.eval_on_selector_all("#map .edge", "els => els.length")
        info["tabs"] = page.eval_on_selector_all(".ftab", "els => els.map(e => e.textContent.trim())")

        page.screenshot(path=str(OUT / "00_full.png"), full_page=True)
        page.locator(".canvas-wrap").screenshot(path=str(OUT / "01_all.png"))

        for flow, name in (("chat", "02_chat"), ("contrib", "03_contrib"), ("ppt", "04_ppt"), ("hazard", "05_hazard"), ("msg", "05b_msg")):
            page.click(f'.ftab[data-flow="{flow}"]')
            page.wait_for_timeout(350)
            info[f"dim_{flow}"] = page.eval_on_selector_all("#map .node.dim", "els => els.length")
            page.locator(".canvas-wrap").screenshot(path=str(OUT / f"{name}.png"))

        # 点节点看详情
        page.click('.ftab[data-flow="hazard"]')
        page.wait_for_timeout(200)
        page.click('.node[data-id="B5"]')
        page.wait_for_timeout(300)
        info["detail"] = page.eval_on_selector("#detail", "e => e.innerText")[:160].replace("\n", " | ")
        page.locator(".canvas-wrap").screenshot(path=str(OUT / "06_select.png"))

        page.screenshot(path=str(OUT / "07_page.png"), full_page=True)
        info["errors"] = errors
        info["files"] = [str(p) for p in sorted(OUT.glob("*.png"))]
        browser.close()

    import json
    print(json.dumps(info, ensure_ascii=False, indent=2))
    ok = info["nodes"] >= 28 and info["edges"] >= 30 and not errors and "隐患识别 Agent" in info.get("detail", "")
    print("\n结论:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
