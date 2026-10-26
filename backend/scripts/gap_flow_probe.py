"""真机验证：缺口 → 采集 → 提交审核 全链路（网络与 LLM 用替身，DB/通知/落盘真实）。"""
import sys
import uuid
from pathlib import Path

B = Path(r"D:\code\aicode\ai智能体开发实战5期\weekend\ShuDao Smart AI Security Assistant\team\backend")
sys.path.insert(0, str(B))
import os
os.chdir(B)

from sqlalchemy import text  # noqa: E402

from src.ai import agent_scout  # noqa: E402
from src.core.db import SessionLocal, engine  # noqa: E402
from src.core.security import hash_password  # noqa: E402
from src.models.conversation import Conversation  # noqa: E402
from src.models.user import User  # noqa: E402
from src.services.notification_service import NotificationService  # noqa: E402

tag = uuid.uuid4().hex[:6]
question = f"矿洞（地下矿山）作业需要配备哪些个体防护装备？（验证{tag}）"

# 1) 造提问者 + 会话
db = SessionLocal()
user = User(username=f"gap_probe_{tag}", hashed_password=hash_password("pass123456"), role="employee")
db.add(user)
db.commit()
db.refresh(user)
conv = Conversation(user_id=user.id, title=question[:20])
db.add(conv)
db.commit()
db.refresh(conv)
print(f"[1] 提问者 #{user.id} / 会话 #{conv.id}")

# 2) 替身：检索词 / 采集 / 总结（不碰外网与 LLM）
pushed = []


def fake_plan(q, methodology):
    return ["地下矿山 个体防护装备 配备 规定"]


def fake_collect(queries):
    res = agent_scout.CollectResult()
    res.pages.append(agent_scout.Page(
        url="https://www.mem.gov.cn/test/mine-ppe.html",
        title="地下矿山个体防护装备配备要求",
        text="地下矿山作业应配备安全帽、防尘口罩、防噪耳塞、反光背心、安全靴等个体防护装备。" * 30,
    ))
    res.visited.append({"url": "https://www.mem.gov.cn/test/mine-ppe.html", "ok": True})
    return res


def fake_summarize(gap, pages):
    body = "\n\n".join(
        f"## 第{i}条\n地下矿山作业应配备安全帽、防尘口罩、防噪耳塞、反光背心与安全靴，并定期检查更换。"
        for i in range(1, 16)
    )
    return f"# 地下矿山作业个体防护装备配备要求\n\n{body}\n\n## 来源\n- https://www.mem.gov.cn/test/mine-ppe.html（2026-10-26）"


agent_scout._plan_queries = fake_plan
agent_scout._collect = fake_collect
agent_scout._summarize = fake_summarize
NotificationService.push = staticmethod(lambda uid, msg, msg_type=None: pushed.append((uid, msg[:40])))

# 3) 跑完整流程
out = agent_scout.run(conv.id, question)
print("[2] run 结果:", out)

# 4) 核对数据库
with engine.connect() as c:
    contrib = c.execute(text(
        "SELECT id,title,status,source_kind,origin_urls,file_path FROM knowledge_contributions "
        "WHERE title LIKE '地下矿山%' ORDER BY id DESC LIMIT 1"
    )).fetchone()
    run_row = c.execute(text(
        "SELECT id,status,contrib_id,methodology_version FROM agent_runs WHERE conversation_id=:c ORDER BY id DESC LIMIT 1"
    ), {"c": conv.id}).fetchone()
print("[3] 贡献行:", contrib)
print("[4] agent_runs:", run_row)
print("[5] 通知:", pushed)

# 5) 清理
if contrib:
    cid, _, _, _, _, fp = contrib
    with engine.begin() as c:
        c.execute(text("DELETE FROM knowledge_contrib_timeline WHERE contrib_id=:i"), {"i": cid})
        c.execute(text("DELETE FROM knowledge_contributions WHERE id=:i"), {"i": cid})
        c.execute(text("DELETE FROM agent_runs WHERE conversation_id=:c"), {"c": conv.id})
        c.execute(text("DELETE FROM chat_messages WHERE conversation_id=:c"), {"c": conv.id})
        c.execute(text("DELETE FROM conversations WHERE id=:i"), {"i": conv.id})
        c.execute(text("DELETE FROM users WHERE id=:i"), {"i": user.id})
    if fp:
        f = Path("uploads") / fp
        if f.exists():
            f.unlink()
    print(f"[6] 已清理贡献 #{cid} / 会话 #{conv.id} / 用户 #{user.id} 及上传文件")
db.close()
