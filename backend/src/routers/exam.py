"""考试工坊路由（计划书 v2，6 个端点）。

端点：
- GET  /api/exam/templates                  试卷模板列表
- POST /api/exam/papers                     创建并生成试卷（同步 30-60s，前端 loading）
- GET  /api/exam/papers                     我的试卷历史（分页）
- GET  /api/exam/papers/{paper_id}          试卷详情（含内容 JSON）
- POST /api/exam/papers/{paper_id}/export   按格式导出（md/docx/pdf）→ 返回下载 URL
- GET  /exam-files/{filename}               静态托管导出文件（main.py 挂载）

注意：生成接口为同步阻塞（LLM 全卷约 30-60s），任务表 status 记录 生成中/已完成/失败；
文档模式（input_type=document）由前端读取 md/txt 文本后走同一 JSON 端点。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user
from src.schemas.exam import (
    ExamExportRequest,
    ExamPaperCreate,
    ExamPaperListOut,
    ExamPaperOut,
    ExamTemplateOut,
)
from src.services.exam_service import exam_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exam", tags=["考试工坊"])


@router.get("/templates", response_model=list[ExamTemplateOut], summary="试卷模板列表（3 套）")
def templates(user: dict = Depends(get_current_user)):
    return exam_service.list_templates()


@router.post("/papers", response_model=ExamPaperOut, status_code=201, summary="创建并生成试卷（同步）")
def create_paper(
    data: ExamPaperCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return exam_service.create_paper(db, user["id"], data)


@router.get("/papers", response_model=ExamPaperListOut, summary="我的试卷历史（分页）")
def list_papers(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    items, total = exam_service.list_papers(db, user["id"], page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/papers/{paper_id}", response_model=ExamPaperOut, summary="试卷详情")
def paper_detail(
    paper_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return exam_service.get_paper(db, user["id"], paper_id)


@router.post("/papers/{paper_id}/export", summary="按格式导出试卷（md/docx/pdf）")
def export_paper(
    paper_id: str,
    body: ExamExportRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return exam_service.export_paper(db, user["id"], paper_id, body.format)
