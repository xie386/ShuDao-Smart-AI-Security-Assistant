"""隐患管理路由（H01–H06）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user, require_roles
from src.schemas.hazard import (
    AcceptRequest,
    AssignRequest,
    HazardCreate,
    HazardDetailOut,
    HazardListOut,
    HazardOut,
    RectifyRequest,
)
from src.services.hazard_service import HazardService

router = APIRouter(prefix="/api/hazards", tags=["隐患管理"])


@router.post("", response_model=HazardOut, status_code=201, summary="H01 隐患上报")
def create_hazard(
    data: HazardCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return HazardService.create(db, data, user["id"])


@router.get("", response_model=HazardListOut, summary="H02 隐患列表（分页/筛选/排序）")
def list_hazards(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    status: Optional[str] = Query(default=None, description="状态：待处理/处理中/待验收/已闭环/已驳回"),
    level: Optional[str] = Query(default=None, description="等级：重大/较大/一般/轻微"),
    hazard_type: Optional[str] = Query(default=None, description="隐患类型"),
    start: Optional[str] = Query(default=None, description="开始时间 YYYY-MM-DD"),
    end: Optional[str] = Query(default=None, description="结束时间 YYYY-MM-DD"),
    sort: str = Query(default="created_at_desc", description="排序：created_at_desc/created_at_asc/level_desc/updated_at_desc"),
    mine: bool = Query(default=False, description="只看我上报的"),
    rectified_by: Optional[int] = Query(default=None, description="按整改人过滤（个人主页『我整改的』）"),
    reviewed_by: Optional[int] = Query(default=None, description="按审批人过滤（个人主页『我审批的』）"),
    db_session: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return HazardService.list_hazards(
        db_session,
        page=page,
        page_size=page_size,
        status_filter=status,
        level=level,
        hazard_type=hazard_type,
        start=start,
        end=end,
        sort=sort,
        user_id=user["id"],
        only_mine=mine,
        rectified_by=rectified_by,
        reviewed_by=reviewed_by,
    )


@router.get("/{hazard_id}", response_model=HazardDetailOut, summary="H03 隐患详情（含时间线）")
def hazard_detail(
    hazard_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    hazard = HazardService.detail(db, hazard_id)
    detail = HazardDetailOut.model_validate(hazard)
    detail.timeline = HazardService.timeline(db, hazard_id)
    return detail


@router.post("/{hazard_id}/assign", response_model=HazardOut, summary="H04 隐患派单（仅 admin）")
def assign_hazard(
    hazard_id: int,
    data: AssignRequest,
    db: Session = Depends(get_db),
    operator: dict = Depends(require_roles("admin")),
):
    return HazardService.assign(db, hazard_id, data, operator)


@router.post("/{hazard_id}/rectify", response_model=HazardOut, summary="H05 整改反馈（责任人）")
def rectify_hazard(
    hazard_id: int,
    data: RectifyRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return HazardService.rectify(db, hazard_id, data, user)


@router.post("/{hazard_id}/accept", response_model=HazardOut, summary="H06 隐患验收（仅 admin）")
def accept_hazard(
    hazard_id: int,
    data: AcceptRequest,
    db: Session = Depends(get_db),
    operator: dict = Depends(require_roles("admin")),
):
    return HazardService.accept(db, hazard_id, data, operator)
