"""Agent 贡献提交（v2.0 大点 04 · 裁定 7/8）。

与人工贡献共用同一张表、同一套审核流程：Agent 只多三个字段
（`source_kind=agent`、`source_agent_id`、`origin_urls`），**没有任何自动入库通道**。

认证：
- 进程内（chat 的网搜工具）直接调用 `submit_agent(..., agent={"id":..,"name":..})`；
- 外部 HTTP 用 `X-Agent-Key`，通过 `require_agent` 依赖按 sha256 比对 `agent_clients.api_key_hash`。
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.agent import AgentClient
from src.models.knowledge_contrib import KnowledgeContribution, KnowledgeContribTimeline
from src.models.user import User
from src.services.notification_service import TYPE_KB_CONTRIB, NotificationService

logger = logging.getLogger(__name__)

VALID_DOC_TYPES = {"laws", "procedures", "plans", "cases"}
_MIN_MD_CHARS = 200  # Agent 产物下限（审核时的 1000 字门槛仍然生效）


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def authenticate(db: Session, raw_key: str) -> dict:
    """按 API Key 找 Agent 身份；失败抛 401。"""
    if not raw_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 X-Agent-Key")
    row = db.scalar(
        select(AgentClient).where(
            AgentClient.api_key_hash == hash_key(raw_key), AgentClient.enabled == 1
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Agent 凭证无效")
    return {"id": row.id, "name": row.name, "model": row.model, "scopes": row.scopes}


def submit_agent(
    db: Session,
    *,
    title: str,
    doc_type: str,
    intro: str,
    md: str,
    agent: dict,
    origin_urls: list[str] | None = None,
    origin_filename: str | None = None,
) -> KnowledgeContribution:
    """Agent 产物落成一条「待审核」贡献（文件存 uploads/kb_contrib/agent/）。"""
    title = (title or "").strip()[:128]
    md = (md or "").strip()
    if len(title) < 2:
        raise HTTPException(status_code=422, detail="标题至少 2 个字")
    if len(md) < _MIN_MD_CHARS:
        raise HTTPException(status_code=422, detail=f"正文过短（{len(md)} 字 < {_MIN_MD_CHARS}）")
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status_code=422, detail=f"doc_type 非法：{doc_type}")
    intro = (intro or "").strip() or f"由 Agent「{agent['name']}」自动采集并总结，待人工审核。"

    rel = f"kb_contrib/agent/{uuid.uuid4().hex}.md"
    dest = Path(settings.UPLOAD_DIR) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(md, encoding="utf-8")

    contrib = KnowledgeContribution(
        title=title,
        content=md,
        file_intro=intro[:500],
        file_path=rel,
        doc_type=doc_type,
        contributor_id=int(agent.get("owner_user_id") or 0) or _fallback_contributor(db),
        status="待审核",
        origin_filename=(origin_filename or f"{title}.md")[:255],
        source_kind="agent",
        source_agent_id=agent.get("id"),
        origin_urls=json.dumps(origin_urls or [], ensure_ascii=False),
    )
    db.add(contrib)
    db.flush()
    db.add(
        KnowledgeContribTimeline(
            contrib_id=contrib.id,
            action="提交",
            operator_id=None,
            actor_kind="agent",
            note=f"Agent「{agent['name']}」提交（{len(md)} 字，来源 {len(origin_urls or [])} 条），等待管理员审核",
        )
    )
    db.commit()
    db.refresh(contrib)

    for admin_id in db.execute(select(User.id).where(User.role == "admin")).scalars().all():
        NotificationService.push(
            admin_id,
            f"【知识待审·Agent】Agent「{agent['name']}」提交：{contrib.title}（贡献 #{contrib.id}），请审核",
            msg_type=TYPE_KB_CONTRIB,
        )
    logger.info("Agent 提交贡献 #%s（%s，%d 字）", contrib.id, agent["name"], len(md))
    return contrib


def _fallback_contributor(db: Session) -> int:
    """`contributor_id` 非空（表约束），Agent 没有 user 行 → 挂到第一个 admin。"""
    admin_id = db.scalar(select(User.id).where(User.role == "admin").limit(1))
    if admin_id is None:
        raise HTTPException(status_code=409, detail="系统没有 admin 账号，无法承载 Agent 贡献")
    return int(admin_id)
