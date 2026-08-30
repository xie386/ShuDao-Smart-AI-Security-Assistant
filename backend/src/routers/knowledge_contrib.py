"""知识贡献路由（K01–K06 + 判定标准文档查看）。

端点：
- POST /api/knowledge-contrib                     K01 提交贡献（全员，multipart：title/doc_type/intro + file）
- GET  /api/knowledge-contrib                     K02 列表（本人/管理员，可筛状态）
- GET  /api/knowledge-contrib/{id}                K03 详情（含 timeline，本人或 admin）
- POST /api/knowledge-contrib/{id}/review         K06 审核（仅 admin；通过→upsert 入向量库）
- POST /api/knowledge-contrib/{id}/review-preview K05 子智能体预筛（仅 admin，不写库）
- GET  /api/knowledge-contrib/review-standard     判定标准文档（管理员审批界面查看入口）
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user, require_roles
from src.schemas.knowledge_contrib import (
    KnowledgeContribListOut,
    KnowledgeContribOut,
    ReviewPreviewOut,
    ReviewRequest,
    ReviewStandardOut,
)
from src.services.knowledge_contrib_service import KnowledgeContribService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge-contrib", tags=["知识贡献"])

# 判定标准文档（硬性规章依据，AI 辅助之外的第二重保障）
_REVIEW_STANDARD = (
    Path(__file__).resolve().parents[2] / "docs" / "施工安全领域文件鉴别判定标准.md"
)


@router.post("", response_model=KnowledgeContribOut, status_code=201, summary="K01 提交知识贡献（全员，上传文件）")
def submit_contrib(
    file: UploadFile = File(..., description="知识文件（md/txt/docx/pdf，≤10MB）"),
    title: str = Form(..., min_length=2, max_length=128, description="知识标题"),
    doc_type: str = Form("laws", description="laws/procedures/plans/cases"),
    intro: str = Form(..., description="文件简介（上传者编写，2-500 字）"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return KnowledgeContribService.submit(db, title, doc_type, intro, file, user["id"])


@router.get("", response_model=KnowledgeContribListOut, summary="K02 贡献列表（本人/管理员）")
def list_contribs(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    status_filter: Optional[str] = Query(default=None, description="状态：待审核/已入库/已驳回（仅 admin 生效）"),
    mine: bool = Query(default=False, description="只看我提交的"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return KnowledgeContribService.list_for(
        db,
        user_id=user["id"],
        role=user["role"],
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        only_mine=mine,
    )


@router.get("/review-standard", response_model=ReviewStandardOut, summary="判定标准文档（管理员查看入口）")
def review_standard(user: dict = Depends(get_current_user)):
    if not _REVIEW_STANDARD.exists():
        raise HTTPException(status_code=404, detail="判定标准文档不存在")
    return ReviewStandardOut(
        filename=_REVIEW_STANDARD.name,
        content=_REVIEW_STANDARD.read_text(encoding="utf-8"),
    )


@router.get("/{contrib_id}", response_model=KnowledgeContribOut, summary="K03 贡献详情（含 timeline）")
def contrib_detail(
    contrib_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    contrib = KnowledgeContribService.detail(db, contrib_id, user["id"], user["role"])
    out = KnowledgeContribOut.model_validate(contrib)
    out.review_by = contrib.review_by
    return out


@router.get("/{contrib_id}/timeline", summary="K03 进度时间线")
def contrib_timeline(
    contrib_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    KnowledgeContribService.detail(db, contrib_id, user["id"], user["role"])
    return KnowledgeContribService.timeline(db, contrib_id)


@router.post("/{contrib_id}/review", response_model=KnowledgeContribOut, summary="K06 审核（仅 admin，通过则入向量库）")
def review_contrib(
    contrib_id: int,
    data: ReviewRequest,
    db: Session = Depends(get_db),
    operator: dict = Depends(require_roles("admin")),
):
    return KnowledgeContribService.review(db, contrib_id, data, operator)


@router.post("/{contrib_id}/review-preview", response_model=ReviewPreviewOut, summary="K05 子智能体预筛（仅 admin，不写库）")
def preview_contrib(
    contrib_id: int,
    db: Session = Depends(get_db),
    operator: dict = Depends(require_roles("admin")),
):
    return KnowledgeContribService.review_preview(db, contrib_id)
