"""06 真机验收探针：SSE 识别 → 标注图烘焙 → 上报落库 → 详情回读 → 清理。

不依赖登录密码：临时覆盖 get_current_user 依赖。
用法：PYTHONPATH= C:/python/python.exe scratch/probe06_stream.py [图片相对路径]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from src.core.security import get_current_user  # noqa: E402
from src.ai import hazard_agent  # noqa: E402
from src.main import app  # noqa: E402

FAKE_USER = {"id": 1, "username": "probe06", "role": "admin", "real_name": "探针"}


def frames(resp):
    """逐帧解析 SSE。"""
    buf = ""
    for chunk in resp.iter_text():
        buf += chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            line = next((l for l in raw.split("\n") if l.startswith("data:")), None)
            if not line:
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


def main() -> int:
    gap_mode = "--gap" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    img = args[0] if args else "uploads/486024852c2947919ed8c3bb078f0aa4.jpg"
    path = Path(img)
    if not path.exists():
        print(f"图片不存在：{path}")
        return 2

    from src.core.config import settings

    if gap_mode:                      # 强制缺口：阈值抬到 0.999 且关缓存，验证网搜链路
        settings.HAZARD_GAP_THRESHOLD = 0.999
        settings.HAZARD_CACHE_TTL = 0
        hazard_agent.GAP_THRESHOLD = 0.999
        print(">>> 缺口模式（阈值 0.999，缓存关闭）")

    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    t0 = time.perf_counter()
    result = None
    with TestClient(app) as client:
        with open(path, "rb") as fh:
            with client.stream(
                "POST", "/api/vision/hazard/stream",
                files={"file": (path.name, fh, "image/jpeg")},
                data={"scene_description": "施工便道旁，围挡与沟渠"},
            ) as resp:
                print("HTTP", resp.status_code)
                for ev in frames(resp):
                    dt = round(time.perf_counter() - t0, 1)
                    stage = ev.get("stage")
                    if stage == "rag_done":
                        print(f"[{dt:>6}s] rag_done gaps={ev.get('gaps')} top={[(t['index'], t['score']) for t in ev.get('top', [])]}")
                    elif stage == "websearch_done":
                        print(f"[{dt:>6}s] websearch_done ok={ev.get('ok')} hosts={ev.get('hosts')} contrib={ev.get('contrib_id')} note={ev.get('note')}")
                    elif stage == "done":
                        result = ev.get("result") or {}
                        print(f"[{dt:>6}s] done")
                    else:
                        print(f"[{dt:>6}s] {stage} {str(ev.get('msg') or '')[:60]}")

        if not result:
            print("未收到结果")
            return 1

        a = result.get("analysis") or result
        hazards = a.get("hazards") or []
        print("\n=== 识别结果 ===")
        print(f"图片 {result.get('image_width')}x{result.get('image_height')} | boxes={result.get('boxes_url')} | 识别+编排 {a.get('elapsed_s') or result.get('elapsed_s')}s")
        print(f"gap = {json.dumps(result.get('gap') or a.get('gap'), ensure_ascii=False)[:300]}")
        print(f"有依据 {sum(1 for h in hazards if h.get('grounded'))}/{len(hazards)} 条")
        for i, h in enumerate(hazards, 1):
            loc = h.get("location")
            print(f"#{i} [{h.get('level')}/{h.get('category')}] {str(h.get('description'))[:40]}")
            print(f"   location={loc} grounded={h.get('grounded')} refs={h.get('refs')}")
            print(f"   suggestion={h.get('suggestion')} | gap={h.get('gap_note')}")

        # 标注图烘焙
        r = client.post("/api/vision/annotate", json={
            "image_url": result.get("image_url"),
            "hazards": hazards,
            "labels": "index",
        })
        print("\nannotate:", r.status_code, r.text[:200])
        anno = (r.json() or {}).get("annotated_url") if r.status_code == 200 else None
        if anno:
            p = Path("uploads") / Path(anno).name
            print(f"标注图体积 {p.stat().st_size / 1024:.0f} KB")

        # 上报 + 落库
        body = {
            "title": "探针：AI 识别上报验证",
            "description": "由 probe06_stream.py 生成，用于验证 06 落库与详情回读，随后删除。",
            "level": hazards[0].get("level") if hazards else "一般",
            "hazard_type": (hazards[0].get("category") if hazards else None),
            "location": "探针位置",
            "image_urls": [result.get("image_url")],
            "annotated_image": anno,
            "ai_analysis": a,
        }
        r = client.post("/api/hazards", json=body)
        print("create:", r.status_code)
        if r.status_code != 201:
            print(r.text[:300])
            return 1
        hid = r.json()["id"]
        print("hazard id =", hid)

        d = client.get(f"/api/hazards/{hid}").json()
        print("detail: annotated_url =", d.get("annotated_url"), "| ai_hazards =", len(d.get("ai_hazards") or []))
        print("detail image_urls =", d.get("image_urls"))
        lst = client.get("/api/hazards?page=1&page_size=5").json()
        hit = [x for x in lst.get("items", []) if x["id"] == hid]
        print("list item annotated_url =", hit[0].get("annotated_url") if hit else "未命中")
        print(f"\n总耗时 {time.perf_counter() - t0:.1f}s")

    # 清理（只删本次探针创建的行）
    from sqlalchemy import text

    from src.core.db import engine

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM hazard_ai_analysis WHERE hazard_id = :i"), {"i": hid})
        conn.execute(text("DELETE FROM hazard_timeline WHERE hazard_id = :i"), {"i": hid})
        conn.execute(text("DELETE FROM hazards WHERE id = :i"), {"i": hid})
    print(f"已清理探针隐患 #{hid} 及其分析行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
