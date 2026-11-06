"""07 前端 DOM 验证：消息与记录（打桩 api，不连后端）。

检查：tab 切换、消息渲染（相对时间/未读样式/跳转）、角标、记录列表、记录详情弹窗。
用法：C:/python/python.exe scratch/verify07_dom.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

FRONT = Path(__file__).resolve().parent.parent.parent / "front"
PORT = 8898
OUT = Path(os.environ.get("LOCALAPPDATA", ".")) / "Temp" / "mr_dom"
OUT.mkdir(parents=True, exist_ok=True)

NOW = int(time.time())
MESSAGES = [
    {"id": "m1", "type": "report", "message": "用户 #101 上报新隐患：3号脚手架横杆缺失（隐患 #901），请及时派单",
     "hazard_id": 901, "contrib_id": None, "created_at": NOW - 120, "expire_at": None, "read": False, "status": None},
    {"id": "m2", "type": "rectify", "message": "隐患 3号脚手架横杆缺失（#901）已整改完成，请及时验收",
     "hazard_id": 901, "contrib_id": None, "created_at": NOW - 3000, "expire_at": None, "read": False, "status": None},
    {"id": "m3", "type": "accept", "message": "【验收驳回】3号脚手架横杆缺失（隐患 #901）：挡脚板高度不足",
     "hazard_id": 901, "contrib_id": None, "created_at": NOW - 7200, "expire_at": None, "read": True, "status": "rejected"},
    {"id": "m4", "type": "final", "message": "【最终处理结果】3号脚手架横杆缺失（隐患 #901）已闭环：复检合格",
     "hazard_id": 901, "contrib_id": None, "created_at": NOW - 90, "expire_at": NOW + 86000, "read": False, "status": None},
    {"id": "m5", "type": "kb_contrib", "message": "【知识已入库】您的贡献 高处作业防护要点（贡献 #77）",
     "hazard_id": None, "contrib_id": 77, "created_at": NOW - 200000, "expire_at": NOW + 80000, "read": True, "status": "passed"},
]

RECORDS = [
    {"id": 11, "kind": "hazard_closed", "ref_id": 901, "title": "3号脚手架横杆缺失",
     "summary": "一般 · 高处作业 · 上报人 #101 · 整改人 #202 · 驳回 1 次", "status": "已闭环",
     "created_at": "2026-11-06 20:10:00", "user_id": 101},
    {"id": 12, "kind": "kb_contrib", "ref_id": 77, "title": "高处作业防护要点",
     "summary": "用户贡献 · procedures · 已入库 · 6 块", "status": "已入库",
     "created_at": "2026-11-05 09:00:00", "user_id": 202},
]

RECORD_DETAIL = {
    "id": 11, "kind": "hazard_closed", "ref_id": 901, "title": "3号脚手架横杆缺失",
    "summary": RECORDS[0]["summary"], "status": "已闭环", "created_at": "2026-11-06 20:10:00",
    "payload": {
        "hazard": {"id": 901, "title": "3号脚手架横杆缺失", "description": "3 号脚手架东侧第二层横杆缺失，作业人员在其上施工。",
                   "level": "一般", "hazard_type": "高处作业", "location": "3 号脚手架", "status": "已闭环",
                   "reporter_id": 101, "assignee_id": 202, "created_at": "2026-11-06 18:00:00"},
        "timeline": [
            {"action": "上报", "operator_id": 101, "note": "提交隐患上报", "created_at": "2026-11-06 18:00:00"},
            {"action": "派单", "operator_id": 1, "note": "指派给用户 202", "created_at": "2026-11-06 18:05:00"},
            {"action": "整改反馈", "operator_id": 202, "note": "已补装横杆", "created_at": "2026-11-06 19:00:00"},
            {"action": "验收驳回", "operator_id": 1, "note": "挡脚板高度不足", "created_at": "2026-11-06 19:30:00"},
            {"action": "整改反馈", "operator_id": 202, "note": "挡脚板加高 18cm", "created_at": "2026-11-06 20:00:00"},
            {"action": "验收通过", "operator_id": 1, "note": "复检合格", "created_at": "2026-11-06 20:10:00"},
        ],
        "rectify": {"measures": "挡脚板加高 18cm", "at": "2026-11-06 20:00:00"},
        "accept": {"status": "已闭环", "note": "复检合格", "at": "2026-11-06 20:10:00", "reject_count": 1},
        "ai": {"hazards": [{"level": "一般", "category": "物的不安全状态", "description": "脚手架横杆缺失",
                            "suggestion": "按方案补装横杆并经验收后使用"}], "annotated_url": None},
    },
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
            page.wait_for_timeout(500)

            page.evaluate("""([messages, records, detail]) => {
                try { token = 'dom'; } catch (e) {}
                document.getElementById('login-view').style.display = 'none';
                document.getElementById('app-view').classList.remove('hidden');
                if (window.me) { window.me.role = 'admin'; } else { me = { id: 1, role: 'admin', username: 'dom' }; }
                window.__read = [];
                window.api = async (p, opt) => {
                    const url = String(p);
                    let body = {};
                    if (url.startsWith('/api/notifications/count')) body = { unread: 3, total: 5, latest: null };
                    else if (url.startsWith('/api/notifications/read')) { body = { unread: 2 }; window.__read.push(opt && opt.body); }
                    else if (url.startsWith('/api/notifications')) body = { total: 5, unread: 3, items: messages };
                    else if (url.startsWith('/api/records/')) body = detail;
                    else if (url.startsWith('/api/records')) body = { total: records.length, page: 1, page_size: 10, items: records };
                    return { ok: true, status: 200, json: async () => body };
                };
            }""", [MESSAGES, RECORDS, RECORD_DETAIL])

            page.evaluate("() => { location.hash = '#/notifications'; }")
            page.wait_for_timeout(700)
            page.evaluate("() => { loadNotifyCount(); }")
            page.wait_for_timeout(300)

            res = {}
            res["tabs"] = page.eval_on_selector_all("#mr-tabs .tab", "els => els.map(e => e.textContent.trim())")
            res["nav_label"] = page.eval_on_selector('[data-nav="notifications"]', "e => e.textContent.trim().replace(/\\s+/g,' ')")
            res["badge"] = page.eval_on_selector("#notify-badge", "e => e.textContent")
            res["msg_count"] = page.eval_on_selector_all("#notify-list .notify-item", "els => els.length")
            res["unread_count"] = page.eval_on_selector_all("#notify-list .notify-item.unread", "els => els.length")
            res["times"] = page.eval_on_selector_all("#notify-list .notify-time", "els => els.map(e => e.textContent.trim())")
            res["read_calls"] = page.evaluate("() => window.__read.length")
            res["has_jump"] = page.eval_on_selector_all("#notify-list .notify-item", "els => els.filter(e => e.getAttribute('onclick')).length")
            page.locator("#page-notifications").screenshot(path=str(OUT / "01_msg.png"))

            # 记录 tab
            page.evaluate("() => switchMr('rec')")
            page.wait_for_timeout(600)
            res["rec_count"] = page.eval_on_selector_all("#rec-list .rec-item", "els => els.length")
            res["rec_page_text"] = page.eval_on_selector("#rec-page", "e => e.textContent")
            page.locator("#page-notifications").screenshot(path=str(OUT / "02_rec.png"))

            # 记录详情弹窗
            page.evaluate("() => openRecord(11)")
            page.wait_for_timeout(600)
            res["modal_open"] = page.eval_on_selector("#modal-record", "e => !e.classList.contains('hidden')")
            res["modal_title"] = page.eval_on_selector("#rec-title", "e => e.textContent")
            res["modal_text"] = page.eval_on_selector("#rec-body", "e => e.innerText").replace("\n", " | ")[:400]
            page.locator("#modal-record .modal").screenshot(path=str(OUT / "03_modal.png"))

            res["page_errors"] = errors
            res["screenshots"] = [str(OUT / f) for f in ("01_msg.png", "02_rec.png", "03_modal.png")]
            browser.close()

        print(json.dumps(res, ensure_ascii=False, indent=2))
        ok = (
            res["msg_count"] == 5
            and res["unread_count"] >= 3
            and res["read_calls"] >= 1
            and res["has_jump"] == 5
            and "消息与记录" in res["nav_label"]
            and res["badge"].strip() == "（3）"
            and res["rec_count"] == 2
            and res["modal_open"]
            and "驳回 1 次" in res["modal_text"]
            and not res["page_errors"]
        )
        print("\n结论:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
