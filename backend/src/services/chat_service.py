"""AI 问答业务（A01–A04）：会话管理 / 历史拼接 / 消息落库。

设计（AI_SOLUTION §3.2）：
- 会话历史存 MySQL（conversations + chat_messages），多轮拼接 {history} 实现 ≥5 轮上下文；
- 历史截断：最近 5 轮（10 条）注入 prompt，防上下文膨胀（AI_SOLUTION §8）；
- A06 引用：ChatMessage.refs 字段保留，D3 接 RAG 后填充；
- SSE 流式：assistant 消息在流结束后落库（独立 SessionLocal，避免依赖会话随响应关闭）。
"""
import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.conversation import ChatMessage, Conversation
from src.schemas.chat import ChatRequest, ConversationOut, MessageOut, QuickQuestionOut

logger = logging.getLogger(__name__)

MAX_HISTORY_ROUNDS = 5  # 注入 prompt 的最近轮数
HISTORY_LIMIT = MAX_HISTORY_ROUNDS * 2  # 消息条数（user+assistant）


class ChatService:
    # ---------- 会话管理（A02） ----------

    @staticmethod
    def list_conversations(db: Session, user_id: int) -> list[ConversationOut]:
        convs = db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
        ).all()
        return [ConversationOut.model_validate(c) for c in convs]

    @staticmethod
    def get_or_create_conversation(
        db: Session, user_id: int, data: ChatRequest
    ) -> Conversation:
        """按 conversation_id 取会话（校验归属），为空则新建。"""
        if data.conversation_id:
            conv = db.get(Conversation, data.conversation_id)
            if not conv or conv.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或不属于当前用户"
                )
            return conv
        conv = Conversation(
            user_id=user_id,
            title=data.title or (data.question[:20] + "…" if len(data.question) > 20 else data.question),
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return conv

    @staticmethod
    def delete_conversation(db: Session, user_id: int, conversation_id: int) -> None:
        conv = db.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或不属于当前用户")
        db.delete(conv)  # chat_messages 有 FK，级联需数据库约束；先手动删消息
        for msg in db.scalars(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        ):
            db.delete(msg)
        db.commit()

    @staticmethod
    def list_messages(db: Session, user_id: int, conversation_id: int) -> list[MessageOut]:
        conv = db.get(Conversation, conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或不属于当前用户")
        msgs = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
        ).all()
        return [MessageOut.model_validate(m) for m in msgs]

    # ---------- 历史与消息（A01 多轮） ----------

    @staticmethod
    def build_history(db: Session, conversation_id: int) -> str:
        """取最近 5 轮消息拼成对话历史文本（供 prompt 的 {history}）。

        v2.0 大点 01：user 行若带预处理结果（存于 refs），拼成
        「用户: 原话（检索式：xxx）」，让模型同时看到「问的是什么」和「检索成了什么」。
        """
        msgs = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
        lines = []
        for m in reversed(msgs):  # 恢复时间顺序
            role = "用户" if m.role == "user" else "助手"
            line = f"{role}: {m.content[:200]}"
            if m.role == "user" and m.refs:
                try:
                    import json as _json

                    obj = _json.loads(m.refs)
                    pre = (obj or {}).get("preprocessed") or {}
                    decl = (pre.get("declarative") or "").strip()
                    if decl and decl != m.content.strip():
                        line += f"（检索式：{decl[:120]}）"
                except Exception:  # noqa: BLE001 —— refs 不是 JSON 时忽略
                    pass
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def has_messages(db: Session, conversation_id: int) -> bool:
        """会话里是否已有消息（v2.0 大点 01：区分「首问」与「追问」，退化路径也靠它）。"""
        return bool(
            db.scalar(
                select(ChatMessage.id)
                .where(ChatMessage.conversation_id == conversation_id)
                .limit(1)
            )
        )

    @staticmethod
    def last_round_hint(db: Session, conversation_id: int, answer_chars: int = 200) -> str:
        """意图判定用的「上一轮」摘要（v2.0 大点 03）。

        **必须在保存本轮用户消息之前调用**，否则取到的是本轮自己。
        返回：上一轮问题全文 + 上一轮回答前 `answer_chars` 字；没有历史则空串。
        """
        msgs = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id.desc())
            .limit(4)
        ).all()
        if not msgs:
            return ""
        last_q = next((m for m in msgs if m.role == "user"), None)
        last_a = next((m for m in msgs if m.role == "assistant"), None)
        parts: list[str] = []
        if last_q is not None:
            parts.append("上一轮问题：" + (last_q.content or "").strip())
        if last_a is not None:
            parts.append("上一轮回答开头：" + (last_a.content or "").strip()[:answer_chars])
        return "\n".join(parts)

    @staticmethod
    def context_digest(db: Session, conversation_id: int, rounds: int = 3) -> dict:
        """Redis 不可用时给判定器的「会话上下文摘要」（v2.0 大点 01）。

        只读 DB：
        - history：最近 rounds 轮问答（用户问题原话 + 答案前 200 字）
        - cited_docs：最近一条 assistant 消息 refs 里已引用的 doc#章节
        返回 {"history": str, "cited_docs": [str]}
        """
        limit = max(2, rounds * 2)
        msgs = db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        ).all()
        msgs = list(reversed(msgs))

        lines: list[str] = []
        cited: list[str] = []
        for m in msgs:
            role = "用户" if m.role == "user" else "助手"
            lines.append(f"{role}: {m.content[:200]}")
        for m in reversed(msgs):  # 最近的 assistant 在前
            if m.role != "assistant" or not m.refs:
                continue
            try:
                import json as _json

                refs = _json.loads(m.refs)
                if isinstance(refs, list):
                    for r in refs:
                        label = (r or {}).get("doc", "")
                        ch = (r or {}).get("chapter") or (r or {}).get("article_no")
                        if label:
                            cited.append(f"{label}#{ch}" if ch else label)
                break
            except Exception:  # noqa: BLE001
                continue
        return {"history": "\n".join(lines), "cited_docs": cited[:10]}

    @staticmethod
    def save_message(
        db: Session, conversation_id: int, role: str, content: str, refs: str | None = None
    ) -> ChatMessage:
        msg = ChatMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            refs=refs,
        )
        db.add(msg)
        db.commit()
        return msg

    @staticmethod
    def update_message_refs(db: Session, message_id: int, refs: str) -> None:
        """补写某条消息的 refs（v2.0 大点 01：user 行存预处理结果，复用现有字段不改表）。"""
        msg = db.get(ChatMessage, message_id)
        if msg is None:
            return
        msg.refs = refs
        db.commit()

    @staticmethod
    def rate_message(db: Session, user_id: int, message_id: int, rating: int) -> ChatMessage:
        """A07 点赞/踩：仅允许对本用户会话内的消息评分（rating=0 取消）。"""
        msg = db.get(ChatMessage, message_id)
        if not msg:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="消息不存在")
        conv = db.get(Conversation, msg.conversation_id)
        if not conv or conv.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权操作该消息"
            )
        msg.rating = rating if rating != 0 else None
        db.commit()
        db.refresh(msg)
        return msg

    # ---------- A04 快捷提问 ----------

    @staticmethod
    def quick_questions() -> list[QuickQuestionOut]:
        return [
            QuickQuestionOut(label="高处作业要求", question="高处作业有哪些安全要求？"),
            QuickQuestionOut(label="安全带使用", question="安全带的正确使用方法是什么？"),
            QuickQuestionOut(label="脚手架检查", question="脚手架搭设完成后需要检查什么？"),
            QuickQuestionOut(label="动火作业审批", question="动火作业需要办理什么手续？"),
            QuickQuestionOut(label="应急预案", question="发生火灾事故应如何应急处置？"),
        ]
