"""记录接口（v2.0 大点 07 · 裁定 7/11）。

- `GET /api/records?kind=&page=&page_size=&q=` → 分页查询（服务端按角色过滤）
- `GET /api/records/{id}` → 详情（快照 payload）

可见性：admin 全部；safety_officer 可看全部**闭环隐患**、贡献记录只看自己；
employee 只看自己上报的隐患和自己贡献的记录。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user
from src.services import record_service

router = APIRouter(prefix="/api/records", tags=["消息与记录"])


@router.get("", summary="记录列表（已闭环隐患 / 知识贡献）")
def list_records(
    kind: Optional[str] = Query(default=None, description="hazard_closed / kb_contrib，空=全部"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    q: Optional[str] = Query(default=None, description="标题/摘要关键词"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return record_service.list_records(
        db, user, kind=kind, page=page, page_size=page_size, q=q
    )


@router.get("/{record_id}", summary="记录详情（快照）")
def get_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    row = record_service.get_record(db, user, record_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在或无权查看")
    return row
