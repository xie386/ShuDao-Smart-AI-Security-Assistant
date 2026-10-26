"""v2.0 大点 03 真机探针：闲聊门禁 / 知识问答 / 指代追问 / 审计 / Redis 缓存隔离。"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8011"
B = Path(r"D:\code\aicode\ai智能体开发实战5期\weekend\ShuDao Smart AI Security Assistant\team\backend")

PWD = "pass123456"


def token() -> str:
    """注册一个一次性探针账号并登录（不碰真实账号，避免凭证外泄）。"""
    import uuid

    username = f"intent_probe_{uuid.uuid4().hex[:8]}"
    with httpx.Client(timeout=30, trust_env=False) as c:
        c.post(f"{BASE}/api/auth/register", json={"username": username, "password": PWD})
        r = c.post(f"{BASE}/api/auth/login", json={"username": username, "password": PWD})
        r.raise_for_status()
        print("probe user:", username)
        return r.json()["access_token"]


def chat(tok: str, question: str, conv_id: int | None = None) -> dict:
    """发一轮问题，收集 meta / 首字时延 / 文本 / 事件数。"""
    payload = {"question": question, "conversation_id": conv_id}
    t0 = time.perf_counter()
    first = None
    text, meta, events, conv = [], None, 0, None
    with httpx.Client(timeout=600, trust_env=False) as c:
        with c.stream("POST", f"{BASE}/api/chat", json=payload,
                      headers={"Authorization": f"Bearer {tok}"}) as r:
            if r.status_code != 200:
                return {"status": r.status_code, "error": r.read().decode("utf-8", "replace")[:300]}
            buf = ""
            for chunk in r.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.splitlines():
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if body in ("", "[DONE]"):
                            continue
                        try:
                            obj = json.loads(body)
                        except json.JSONDecodeError:
                            continue
                        events += 1
                        if "meta" in obj:
                            meta = obj["meta"]
                        if "delta" in obj:
                            if first is None:
                                first = time.perf_counter() - t0
                            text.append(obj["delta"])
                        if "error" in obj:
                            text.append(f"[ERROR]{obj['error']}")
    return {
        "status": 200, "meta": meta, "events": events,
        "first_s": round(first, 2) if first else None,
        "total_s": round(time.perf_counter() - t0, 2),
        "answer": "".join(text),
    }


def main() -> None:
    tok = token()
    print("=== 1) 闲聊：在吗（新会话）===")
    r1 = chat(tok, "在吗")
    m1 = r1.get("meta") or {}
    print(f"status={r1['status']} mode={m1.get('mode')} intent={m1.get('intent', {}).get('label')} "
          f"intent_ms={m1.get('intent', {}).get('ms')} sources={m1.get('sources')} "
          f"first={r1.get('first_s')}s total={r1.get('total_s')}s")
    print("answer:", (r1.get("answer") or "")[:120].replace("\n", " "))

    print("\n=== 2) 知识问答：高处作业几米算高处（新会话）===")
    r2 = chat(tok, "高处作业几米算高处")
    m2 = r2.get("meta") or {}
    print(f"status={r2['status']} mode={m2.get('mode')} intent={m2.get('intent', {}).get('label')} "
          f"intent_ms={m2.get('intent', {}).get('ms')} sources={m2.get('sources')} "
          f"ctx={m2.get('context_chars')} first={r2.get('first_s')}s total={r2.get('total_s')}s")
    print("answer:", (r2.get("answer") or "")[:120].replace("\n", " "))

    print("\n=== 3) 指代追问：第一问（同一会话，带上一轮）===")
    convs = httpx.get(f"{BASE}/api/conversations", headers={"Authorization": f"Bearer {tok}"},
                      timeout=30, trust_env=False).json()
    conv_id = max(c["id"] for c in convs)  # 最近创建的会话 = 第 2 步那个
    r3 = chat(tok, "第一问", conv_id=conv_id)
    m3 = r3.get("meta") or {}
    print(f"conv={conv_id} mode={m3.get('mode')} intent={m3.get('intent', {}).get('label')} "
          f"reason={m3.get('intent', {}).get('reason')} sources={m3.get('sources')} "
          f"first={r3.get('first_s')}s total={r3.get('total_s')}s")

    print("\n=== 4) 审计（A08）===")
    audits = {}
    for cid in {max(c["id"] for c in convs)}:
        a = httpx.get(f"{BASE}/api/conversations/{cid}/preprocess-audit",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=30, trust_env=False).json()
        audits[cid] = a
        print(f"conv={cid} rounds={a['total_rounds']} with_intent={a.get('rounds_with_intent')} "
              f"chat_rounds={a.get('chat_rounds')}")
        for rd in a["rounds"]:
            it = rd.get("intent") or {}
            print(f"  Q: {rd['question'][:24]:26s} intent={it.get('label')} ({it.get('ms')}ms) "
                  f"pre={'有' if rd['preprocessed'] else '无'} sources={rd['sources']}")

    print("\n=== 5) Redis：闲聊轮不应写会话 RAG 缓存 ===")
    sys.path.insert(0, str(B))
    os.chdir(B)
    try:
        from src.services import conv_rag_cache

        keys = conv_rag_cache._client().keys("shudao:rag:conv:*") if conv_rag_cache.available() else []
        keys = [k.decode() if isinstance(k, bytes) else str(k) for k in (keys or [])]
        print("available:", conv_rag_cache.available(), "| 现存会话缓存 key 数:", len(keys))
        print("样例:", keys[:3])
    except Exception as e:  # noqa: BLE001
        print("Redis 检查失败:", type(e).__name__, e)


if __name__ == "__main__":
    main()
