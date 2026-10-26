"""查看「问题预处理审计」：按账号或按会话 id 取（v2.0 大点 01）。

数据来源与前端 🔎 按钮**完全一致**：`src/services/preprocess_audit.py::collect()`
（底层是 `chat_messages.references` 里 user 消息的 `{"preprocessed": {...}}`）。

用法（Windows 上必须带 `PYTHONPATH=` 前缀）：
    cd backend

    # ① 按会话 id（最常用：前端 🔎 按钮走的是同一份数据的 HTTP 版本）
    PYTHONPATH= C:\\python\\python.exe scripts/show_preprocessed.py --conv 90

    # ② 按账号看全部会话
    PYTHONPATH= C:\\python\\python.exe scripts/show_preprocessed.py jobana

    # ③ 按账号 + 只看某个会话
    PYTHONPATH= C:\\python\\python.exe scripts/show_preprocessed.py jobana --conv 90

    # ④ 机器可读
    PYTHONPATH= C:\\python\\python.exe scripts/show_preprocessed.py --conv 90 --json

退出码：0 成功；1 参数错误或会话不存在。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.core.db import SessionLocal  # noqa: E402
from src.models.conversation import Conversation  # noqa: E402
from src.models.user import User  # noqa: E402
from src.services.preprocess_audit import collect  # noqa: E402


def _print_payload(payload: dict, title: str = "") -> None:
    head = f"===== 会话 {payload['conversation_id']}"
    if title:
        head += f"「{title}」"
    head += (
        f" · {payload['total_rounds']} 轮"
        f"（有预处理记录 {payload['rounds_with_preprocess']} 轮）====="
    )
    print(head)
    if not payload["rounds"]:
        print("  （该会话没有消息）\n")
        return
    for i, r in enumerate(payload["rounds"], 1):
        print(f"  [{i}] 原问题: {r['question']}")
        pre = r.get("preprocessed")
        if not pre:
            print("      预处理: （无记录 —— v2.0 大点 01 之前的消息）")
        else:
            print(f"      检索式: {pre.get('declarative')}")
            subs = pre.get("subqueries") or []
            print(f"      子查询({len(subs)}): {subs}")
            print(
                f"      split={pre.get('split')} used_llm={pre.get('used_llm')} "
                f"剔除已检索={pre.get('covered_dropped') or []}"
            )
            print(f"      依据: {pre.get('reason')}")
        answered = (
            f"#{r['assistant_message_id']}"
            if r.get("assistant_message_id")
            else "（无，可能生成失败）"
        )
        print(f"      回答: {answered} · 来源 {r.get('sources', 0)} 条 · rating={r.get('rating')}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="查看问题预处理审计（按账号或会话 id）")
    ap.add_argument("username", nargs="?", help="账号名（可省略，此时必须给 --conv）")
    ap.add_argument(
        "--conv",
        "--conversation-id",
        dest="conv",
        type=int,
        default=None,
        help="会话 id；单独使用时不需要账号",
    )
    ap.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    # 兼容 `show_preprocessed.py 90` 这种「裸会话 id」写法
    if args.username and args.username.isdigit() and args.conv is None:
        args.conv, args.username = int(args.username), None

    if not args.username and args.conv is None:
        ap.error("至少要给账号名或 --conv <会话id>")

    db = SessionLocal()
    try:
        # 只给 --conv：脚本是本地运维工具，不额外校验账号归属
        if args.conv is not None and not args.username:
            conv = db.get(Conversation, args.conv)
            if not conv:
                print(f"会话 {args.conv} 不存在")
                return 1
            payload = collect(args.conv, db=db)
            if args.as_json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                _print_payload(payload, conv.title or "")
            return 0

        user = db.scalars(select(User).where(User.username == args.username)).first()
        if not user:
            print("找不到用户:", args.username)
            return 1

        stmt = select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.id)
        if args.conv is not None:
            stmt = stmt.where(Conversation.id == args.conv)
        convs = db.scalars(stmt).all()
        if not convs:
            print(f"用户 {args.username} 没有符合条件的会话")
            return 1

        if args.as_json:
            print(json.dumps([collect(c.id, db=db) for c in convs], ensure_ascii=False, indent=2))
            return 0

        print(f"用户 {user.username} (id={user.id})，会话 {len(convs)} 个\n")
        for conv in convs:
            _print_payload(collect(conv.id, db=db), conv.title or "")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
