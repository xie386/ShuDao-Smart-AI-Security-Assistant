"""H07 统计分析路由。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user
from src.schemas.stats import HazardStatsOut
from src.services.stats_service import StatsService
 
router = APIRouter(prefix="/api/stats", tags=["统计分析"])


@router.get(
    "/hazards",
    response_model=HazardStatsOut,
    summary="隐患统计分析（H07）",
)
def hazard_stats(
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return StatsService.hazard_stats(db)
