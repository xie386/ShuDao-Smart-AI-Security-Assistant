#!/usr/bin/env python
"""D5 消息类型丰富化端到端验证：三类新消息（整改反馈→管理员 / 验收结果→安全员·管理员 / 最终处理结果→全员）。

链路：注册 上报人+责任人+安全员 → admin 上报 → 派单给责任人 → 责任人整改 → 验证管理员收到 rectify →
      安全员验收 → 验证安全员/管理员收到 accept + 全员收到 final。
"""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS = "d5test123"


def _opener():
    """禁用环境代理：本机代理 127.0.0.1:7897 会劫持 localhost 请求。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(method, path, token=None, body=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with _opener().open(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def login(username, password=PASS):
    st, d = req("POST", "/api/auth/login", body={"username": username, "password": password})
    if st == 200:
        return d.get("access_token"), (d.get("user") or {}).get("id")
    return None, None


def notifies(token):
    st, d = req("GET", "/api/notifications", token=token)
    return d.get("items", []) if st == 200 else []


def register(username, real_name):
    return req("POST", "/api/auth/register", body={"username": username, "password": PASS, "real_name": real_name})


def main():
    # 注册（幂等 409 忽略）
    for u, n in (("d5_rep2", "上报人"), ("d5_asg2", "责任人"), ("d5_saf2", "安全员")):
        st, _ = register(u, n)
        print(f"注册 {u}: {st}")

    admin_tk, _ = login("admin", "admin123")
    rep_tk, rep_id = login("d5_rep2")
    asg_tk, asg_id = login("d5_asg2")
    saf_tk, saf_id = login("d5_saf2")
    assert admin_tk and rep_tk and asg_tk and saf_tk, "登录失败"
    print(f"登录成功: rep={rep_id} asg={asg_id} saf={saf_id}")

    # 清空通知
    for tk in (admin_tk, rep_tk, asg_tk, saf_tk):
        req("DELETE", "/api/notifications", token=tk)

    # ① 上报 + 派单
    st, haz = req("POST", "/api/hazards", token=rep_tk, body={
        "title": "D5消息类型-吊篮钢丝绳磨损", "description": "2号吊篮钢丝绳磨损超限", "level": "较大",
        "hazard_type": "吊篮", "location": "5号墩",
    })
    assert st == 201, f"上报失败 {st}"
    hid = haz["id"]
    st, _ = req("POST", f"/api/hazards/{hid}/assign", token=admin_tk, body={"assignee_id": asg_id})
    assert st == 200, f"派单失败 {st}"
    print(f"[1] 隐患 #{hid} 上报并派单给责任人")

    # ② 责任人整改 → 验证：管理员收到 rectify
    st, _ = req("POST", f"/api/hazards/{hid}/rectify", token=asg_tk, body={"rectify_measures": "已更换钢丝绳"})
    assert st == 200, f"整改失败 {st}"
    admin_msgs = [n for n in notifies(admin_tk) if n.get("hazard_id") == hid]
    rect_admin = [n for n in admin_msgs if n.get("type") == "rectify"]
    print(f"[2] 整改后 admin 消息: {len(admin_msgs)} 条, rectify 类 {len(rect_admin)} 条")
    for n in rect_admin:
        print("    -", n["message"][:50])
    assert rect_admin, "❌ 管理员未收到整改反馈"

    # ③ admin 验收通过 → 验证：安全员/管理员收到 accept + 全员收到 final
    st, _ = req("POST", f"/api/hazards/{hid}/accept", token=admin_tk, body={"approved": True, "note": "整改到位"})
    assert st == 200, f"验收失败 {st}"
    for name, tk in (("安全员", saf_tk), ("管理员", admin_tk), ("上报人", rep_tk), ("责任人", asg_tk)):
        msgs = [n for n in notifies(tk) if n.get("hazard_id") == hid]
        acc = [n for n in msgs if n.get("type") == "accept"]
        fin = [n for n in msgs if n.get("type") == "final"]
        print(f"[3] {name} 收到: accept={len(acc)} final={len(fin)} | 全部 {len(msgs)} 条")
        for n in acc + fin:
            print("    -", n["message"][:45])

    assert any(n.get("type") == "accept" for n in notifies(saf_tk)), "❌ 安全员未收到验收结果"
    assert any(n.get("type") == "final" for n in notifies(rep_tk)), "❌ 上报人未收到最终处理结果"
    print("\n🎉 消息类型丰富化验证全部通过！")


if __name__ == "__main__":
    main()
