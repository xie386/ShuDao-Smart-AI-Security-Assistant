"""记录服务：把"已结束的事"落成可翻阅的记录（v2.0 大点 07 · 裁定 5/6/7）。

两类记录：
- `hazard_closed`：隐患闭环快照（基本信息 + 时间线 + 整改 + 验收 + 驳回次数 + AI 建议）
- `kb_contrib`：知识贡献审核结果快照

可见性（服务端强制，裁定 7）：
    hazard_closed: admin 全部 / safety_officer 全部 / 其他仅自己上报
    kb_contrib   : admin 全部 / 其他仅自己贡献
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from src.models.message_record import KIND_CONTRIB, KIND_HAZARD, MessageRecord

logger = logging.getLogger(__name__)

ADMIN = "admin"
SAFETY = "safety_officer"


def _iso(dt) -> str | None:
    return dt.isoformat(sep=" ", timespec="seconds") if dt else None


# --------------------------------------------------------------------------- #
# 快照
# --------------------------------------------------------------------------- #
def snapshot_hazard(db: Session, hazard) -> dict:
    """构造隐患闭环快照（能复核的程度，而不是只存标题）。"""
    from src.models.hazard import HazardTimeline
    from src.services import hazard_analysis_service as haz_ai

    rows = db.scalars(
        select(HazardTimeline)
        .where(HazardTimeline.hazard_id == hazard.id)
        .order_by(HazardTimeline.created_at.asc())
    ).all()
    timeline = [
        {"action": r.action, "operator_id": r.operator_id, "note": r.note, "created_at": _iso(r.created_at)}
        for r in rows
    ]
    reject_count = sum(1 for r in rows if r.action == "验收驳回")

    def _json_list(raw):
        try:
            return json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return []

    ai = haz_ai.load_latest(db, hazard.id)
    return {
        "hazard": {
            "id": hazard.id,
            "title": hazard.title,
            "description": hazard.description,
            "level": hazard.level,
            "hazard_type": hazard.hazard_type,
            "location": hazard.location,
            "status": hazard.status,
            "reporter_id": hazard.reporter_id,
            "assignee_id": hazard.assignee_id,
            "image_urls": _json_list(hazard.image_urls),
            "rectify_images": _json_list(hazard.rectify_images),
            "created_at": _iso(hazard.created_at),
            "updated_at": _iso(hazard.updated_at),
        },
        "timeline": timeline,
        "rectify": {
            "measures": hazard.rectify_measures,
            "at": next((r["created_at"] for r in timeline if r["action"] == "整改反馈"), None),
        },
        "accept": {
            "status": hazard.status,
            "note": next((r["note"] for r in reversed(timeline) if r["action"].startswith("验收")), None),
            "at": next((r["created_at"] for r in reversed(timeline) if r["action"].startswith("验收")), None),
            "reject_count": reject_count,
        },
        "ai": ai,
    }


def snapshot_contrib(db: Session, contrib) -> dict:
    return {
        "contrib": {
            "id": contrib.id,
            "title": contrib.title,
            "doc_type": contrib.doc_type,
            "source_kind": getattr(contrib, "source_kind", "user"),
            "source_agent_id": getattr(contrib, "source_agent_id", None),
            "contributor_id": contrib.contributor_id,
            "status": contrib.status,
            "chunk_count": contrib.chunk_count,
            "file_path": getattr(contrib, "md_path", None),
            "created_at": _iso(getattr(contrib, "created_at", None)),
        },
        "review": {
            "status": contrib.status,
            "note": getattr(contrib, "review_note", None),
            "by": getattr(contrib, "review_by", None),
            "at": _iso(getattr(contrib, "updated_at", None)),
        },
    }


# --------------------------------------------------------------------------- #
# 写（幂等）
# --------------------------------------------------------------------------- #
def _upsert(db: Session, *, kind: str, ref_id: int, user_id: Optional[int], title: str,
            summary: str, status: str, payload: dict) -> Optional[int]:
    """按 (kind, ref_id) 幂等写入：已存在则更新快照，不新增行。"""
    try:
        row = db.scalars(
            select(MessageRecord).where(MessageRecord.kind == kind, MessageRecord.ref_id == ref_id)
        ).first()
        body = json.dumps(payload, ensure_ascii=False, default=str)
        if row:
            row.title = title[:200]
            row.summary = summary[:500]
            row.status = status[:24]
            row.payload_json = body
            row.user_id = user_id
            db.commit()
            return row.id
        row = MessageRecord(
            kind=kind, ref_id=ref_id, user_id=user_id, title=title[:200],
            summary=summary[:500], status=status[:24], payload_json=body,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    except Exception as exc:  # noqa: BLE001 —— 记录失败不能影响业务结果
        db.rollback()
        logger.warning("记录写入失败(%s#%s)：%s", kind, ref_id, exc)
        return None


def record_hazard_closed(db: Session, hazard) -> Optional[int]:
    """隐患验收通过时调用（放在验收事务提交之后）。"""
    payload = snapshot_hazard(db, hazard)
    reject = payload["accept"]["reject_count"]
    summary = (
        f"{hazard.level} · {hazard.hazard_type or '未分类'} · 上报人 #{hazard.reporter_id}"
        f" · 整改人 #{hazard.assignee_id or '-'}"
        + (f" · 驳回 {reject} 次" if reject else "")
    )
    return _upsert(
        db, kind=KIND_HAZARD, ref_id=hazard.id, user_id=hazard.reporter_id,
        title=hazard.title, summary=summary, status=hazard.status, payload=payload,
    )


def record_contrib(db: Session, contrib) -> Optional[int]:
    """知识贡献审核结束（通过/驳回）时调用。"""
    payload = snapshot_contrib(db, contrib)
    src = "Agent" if getattr(contrib, "source_kind", "user") == "agent" else "用户"
    summary = f"{src}贡献 · {contrib.doc_type} · {contrib.status}" + (
        f" · {contrib.chunk_count} 块" if getattr(contrib, "chunk_count", None) else ""
    )
    return _upsert(
        db, kind=KIND_CONTRIB, ref_id=contrib.id, user_id=contrib.contributor_id,
        title=contrib.title, summary=summary, status=contrib.status, payload=payload,
    )


# --------------------------------------------------------------------------- #
# 读（带可见性）
# --------------------------------------------------------------------------- #
def _can_view_all(user: dict, kind: str) -> bool:
    role = (user or {}).get("role")
    if role == ADMIN:
        return True
    if kind == KIND_HAZARD and role == SAFETY:
        return True          # 裁定 7：安全员可看全部闭环隐患（含不是自己做的）
    return False


def list_records(db: Session, user: dict, *, kind: Optional[str] = None,
                 page: int = 1, page_size: int = 10, q: Optional[str] = None) -> dict:
    """分页查询记录；不可见的数据在 SQL 层就被过滤掉。"""
    kinds = [kind] if kind in (KIND_HAZARD, KIND_CONTRIB) else [KIND_HAZARD, KIND_CONTRIB]
    clauses = []
    for k in kinds:
        cond = [MessageRecord.kind == k]
        if not _can_view_all(user, k):
            cond.append(MessageRecord.user_id == (user or {}).get("id"))
        clauses.append(and_(*cond))
    stmt = select(MessageRecord).where(or_(*clauses))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(MessageRecord.title.like(like), MessageRecord.summary.like(like)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": r.id, "kind": r.kind, "ref_id": r.ref_id, "title": r.title,
                "summary": r.summary, "status": r.status, "created_at": _iso(r.created_at),
                "user_id": r.user_id,
            }
            for r in rows
        ],
    }


def get_record(db: Session, user: dict, record_id: int) -> Optional[dict]:
    row = db.get(MessageRecord, record_id)
    if not row:
        return None
    if not _can_view_all(user, row.kind) and row.user_id != (user or {}).get("id"):
        return None
    try:
        payload = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    return {
        "id": row.id, "kind": row.kind, "ref_id": row.ref_id, "title": row.title,
        "summary": row.summary, "status": row.status, "user_id": row.user_id,
        "created_at": _iso(row.created_at), "payload": payload,
    }
