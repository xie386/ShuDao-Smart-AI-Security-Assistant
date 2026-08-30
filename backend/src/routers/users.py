"""用户列表接口：供隐患派单（H04）选择责任人（D5 A2）。

- 仅 admin 可调（派单仅 admin，用户列表随权限收紧）；
- 默认排除当前用户（派单不会派给自己）；
- ?role= 支持逗号分隔多角色（如 safety_officer,employee）；
- ?q= 搜索：纯数字按 id 精确匹配，否则按 username/real_name 模糊匹配
  （派单责任人可搜索，解决下拉 limit 截断导致 50 后用户不可选的问题）；
- ?limit= 限制条数（默认 100，最多 500），无搜索时按 id 倒序（最近注册优先）。
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import require_roles
from src.models import User
from src.schemas.auth import UserOut

router = APIRouter(prefix="/api/users", tags=["用户"])


@router.get("", response_model=list[UserOut], summary="用户列表（仅 admin，派单选择责任人用）")
def list_users(
    role: Optional[str] = Query(default=None, description="按角色过滤，逗号分隔多值：admin/safety_officer/employee"),
    q: Optional[str] = Query(default=None, description="搜索：纯数字按 id 精确匹配，否则按 username/real_name 模糊匹配"),
    limit: int = Query(default=100, ge=1, le=500, description="返回条数（无搜索时最近注册优先）"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_roles("admin")),
):
    stmt = select(User).where(User.id != user["id"])
    if role:
        roles = [r.strip() for r in role.split(",") if r.strip()]
        if roles:
            stmt = stmt.where(User.role.in_(roles))
    if q and q.strip():
        kw = q.strip()
        if kw.isdigit():
            stmt = stmt.where(User.id == int(kw))
        else:
            like = f"%{kw}%"
            stmt = stmt.where(or_(User.username.like(like), User.real_name.like(like)))
        # 搜索结果按 id 正序（相关性优先于注册时间），limit 放宽
        stmt = stmt.order_by(User.id).limit(min(limit, 100))
    else:
        stmt = stmt.order_by(User.id.desc()).limit(limit)
    return db.execute(stmt).scalars().all()
