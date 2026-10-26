"""真机 E2E：通过 HTTP 走完整 PPT Agent 链路（真实 LLM + RAG + Openverse + AGNES）。

用法（backend/ 目录下）：
    PYTHONPATH= C:\\python\\python.exe probe_ppt_e2e.py [端口] [页数]
前置：后端已在指定端口启动，且已完成检索链路预热。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from src.core.security import create_access_token  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 6
USER_ID = 186                     # jobana
BASE = f"http://127.0.0.1:{PORT}"
H = {"Authorization": f"Bearer {create_access_token(USER_ID, 'safety_officer')}"}

SPEC = {
    "topic": "高处作业安全要点",
    "requirements": "面向一线班组长，突出安全带使用、临边洞口防护与恶劣天气停工条件，语言口语化。",
    "pages": PAGES,
    "words_per_page": 80,
    "allow_overflow": False,
    "template": "builtin_simple",
}


def main() -> int:
    c = httpx.Client(base_url=BASE, headers=H, timeout=180, trust_env=False)

    # 1. 等健康（预热在后台，健康接口不阻塞）
    for _ in range(60):
        try:
            if c.get("/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    caps = c.get("/api/ppt/capabilities")
    print("[capabilities]", caps.status_code, json.dumps(caps.json(), ensure_ascii=False))

    # 2. 建任务（P01 出大纲）
    t0 = time.time()
    r = c.post("/api/ppt", json={"topic": SPEC["topic"], "template": "builtin_simple"})
    print("[create]", r.status_code, f"{time.time()-t0:.1f}s")
    if r.status_code != 201:
        print(r.text[:400])
        return 1
    task = r.json()
    tid = task["id"]
    print("[task]", tid, task["status"], "outline_len=", len(task.get("outline") or ""))

    # 3. 流式生成
    print(f"[agent] POST /api/ppt/{tid}/agent pages={PAGES}")
    stages: dict[str, float] = {}
    events = 0
    done = None
    started = time.time()
    with c.stream("POST", f"/api/ppt/{tid}/agent", json=SPEC) as resp:
        print("[agent] HTTP", resp.status_code, resp.headers.get("content-type"))
        if resp.status_code != 200:
            print(resp.read().decode("utf-8", "ignore")[:500])
            return 1
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            events += 1
            if ev.get("error"):
                print("[error]", ev)
                continue
            if ev.get("stage") and ev["stage"] not in stages:
                stages[ev["stage"]] = round(time.time() - started, 1)
            if ev.get("done"):
                done = ev
                print("[done]", json.dumps(ev, ensure_ascii=False))
            else:
                tag = "!" if ev.get("level") == "warn" else " "
                print(f"{tag}[{ev.get('stage')} {ev.get('progress')}%] {ev.get('msg')}"
                      + (f" shot={ev.get('shot')}" if ev.get("shot") else ""))

    total = round(time.time() - started, 1)
    print(f"\n=== 事件 {events} 条 / 流式总耗时 {total}s ===")
    print("阶段首现时刻:", stages)

    # 4. 结果核验
    detail = c.get(f"/api/ppt/{tid}").json()
    print("[status]", detail["status"], "file_path=", detail.get("file_path"))
    prog = c.get(f"/api/ppt/{tid}/progress").json()
    print("[progress]", json.dumps(prog, ensure_ascii=False))
    ws = c.get(f"/api/ppt/{tid}/workspace").json()
    print("[workspace]", {k: len(v) for k, v in ws.items()})
    if ws.get("shots"):
        shot = c.get(f"/api/ppt/{tid}/assets/shots/{ws['shots'][0]}")
        print("[asset]", ws["shots"][0], shot.status_code, len(shot.content), "bytes")
    dl = c.get(f"/api/ppt/{tid}/download")
    print("[download]", dl.status_code, dl.headers.get("content-disposition"), len(dl.content), "bytes")
    if ws.get("images"):
        print("[sources.json]", (Path(detail["file_path"]).parent.parent / "images" / "sources.json").is_file())
    logs = Path(detail["file_path"]).parent.parent / "logs" / f"{tid}.jsonl"
    print("[log lines]", len(logs.read_text(encoding="utf-8").splitlines()) if logs.exists() else 0)
    print("[OK]" if detail["status"] == "已完成" and done else "[FAIL]")
    return 0 if detail["status"] == "已完成" and done else 1


if __name__ == "__main__":
    raise SystemExit(main())
