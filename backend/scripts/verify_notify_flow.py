#!/usr/bin/env python
"""D5 A3 任务：H08 通知链路端到端验证脚本（Redis 队列在派单/整改/验收时 push）。

流程：
  1. 注册 上报人(d5_rep) + 责任人(d5_asg)（已存在则跳过）
  2. admin 登录 → 上报隐患 → 派单给 d5_asg
  3. 验证：d5_asg 收到「整改任务」通知
  4. d5_asg 登录 → 整改反馈
  5. 验证：d5_rep 收到「待验收」通知
  6. admin 验收通过
  7. 验证：d5_rep + d5_asg 都收到「验收通过」通知
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
PASS = "d5test123"


def _opener():
    """禁用环境代理：本机代理 127.0.0.1:7897 会劫持 localhost 请求（curl 需 --noproxy，urllib 同理）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(method, path, token=None, body=None, form=None):
    url = BASE + path
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    elif form:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"detail": "parse error"}


import urllib.parse  # noqa: E402


def register(username, real_name):
    st, d = req("POST", "/api/auth/register", body={"username": username, "password": PASS, "real_name": real_name})
    return st, d


def login(username, password=PASS):
    st, d = req("POST", "/api/auth/login", body={"username": username, "password": password})
    if st == 200:
        return d.get("access_token"), (d.get("user") or {}).get("id")
    return None, None


def notifies(token, uid):
    st, d = req("GET", "/api/notifications", token=token)
    return d


def main():
    # 1. 注册（幂等：已存在会 409，忽略）
    st1, d1 = register("d5_rep", "D5上报人")
    st2, d2 = register("d5_asg", "D5责任人")
    print(f"[1] 注册 d5_rep: {st1} | d5_asg: {st2}")

    # 登录三账号（admin 密码与测试用户不同）
    admin_tk, _ = login("admin", "admin123")
    rep_tk, rep_id = login("d5_rep")
    asg_tk, asg_id = login("d5_asg")
    assert admin_tk and rep_tk and asg_tk, "登录失败"
    print(f"[2] 三账号登录成功（d5_rep id={rep_id}, d5_asg id={asg_id}）")

    # 清空三账号通知
    for tk in (admin_tk, rep_tk, asg_tk):
        req("DELETE", "/api/notifications", token=tk)
    print("[3] 通知已清空")

    # 2. d5_rep 上报隐患
    st, haz = req("POST", "/api/hazards", token=rep_tk, body={
        "title": "D5通知测试-脚手架横杆缺失",
        "description": "3号脚手架第2层横杆缺失，存在坠落风险",
        "level": "一般",
        "hazard_type": "脚手架",
        "location": "3号桥墩施工区",
    })
    assert st == 201, f"上报失败 {st} {haz}"
    hid = haz["id"]
    print(f"[4] d5_rep 上报隐患 #{hid}")

    # 3. admin 派单给 d5_asg（id 取自登录响应）
    st, h2 = req("POST", f"/api/hazards/{hid}/assign", token=admin_tk, body={"assignee_id": asg_id})
    assert st == 200, f"派单失败 {st} {h2}"
    print(f"[5] admin 派单给 d5_asg(#{asg_id})")

    # 4. 验证 d5_asg 收到通知
    nd = notifies(asg_tk, asg_id)
    msgs = [i.get("message", "") for i in nd.get("items", [])]
    print(f"[6] d5_asg 通知数: {len(msgs)}")
    for m in msgs:
        print("    -", m[:60])
    assert any("整改任务" in m for m in msgs), "❌ 责任人未收到派单通知"

    # 5. d5_asg 整改
    st, h3 = req("POST", f"/api/hazards/{hid}/rectify", token=asg_tk, body={
        "rectify_measures": "已补装横杆并验收稳固",
        "rectify_images": None,
    })
    assert st == 200, f"整改失败 {st} {h3}"
    print("[7] d5_asg 提交整改反馈")

    # 6. 验证 d5_rep 收到待验收通知
    nd = notifies(rep_tk, rep_id)
    msgs = [i.get("message", "") for i in nd.get("items", [])]
    print(f"[8] d5_rep 通知数: {len(msgs)}")
    for m in msgs:
        print("    -", m[:60])
    assert any("待验收" in m for m in msgs), "❌ 上报人未收到待验收通知"

    # 7. admin 验收通过
    st, h4 = req("POST", f"/api/hazards/{hid}/accept", token=admin_tk, body={"approved": True, "note": "整改到位"})
    assert st == 200, f"验收失败 {st} {h4}"
    print("[9] admin 验收通过")

    # 8. 验证双方收到验收通知
    for name, tk, uid in (("d5_rep", rep_tk, rep_id), ("d5_asg", asg_tk, asg_id)):
        nd = notifies(tk, uid)
        msgs = [i.get("message", "") for i in nd.get("items", [])]
        ok = any("验收通过" in m for m in msgs)
        print(f"[10] {name} 收到验收通知: {'✅' if ok else '❌'}")
        for m in msgs:
            print("    -", m[:60])
        assert ok, f"{name} 未收到验收通知"

    print("\n🎉 A3 H08 通知链路端到端验证全部通过！")


if __name__ == "__main__":
    main()
