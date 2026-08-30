"""PPT 工坊路由（P01–P07，D4）。

端点：
- POST /api/ppt                 P01 新建任务（AI 生成大纲）
- GET  /api/ppt                 P03 我的任务列表（分页）
- GET  /api/ppt/{task_id}       P03 任务详情（含大纲/状态/文件）
- PUT  /api/ppt/{task_id}/outline  P02 编辑大纲（整篇替换）
- POST /api/ppt/{task_id}/generate P03 触发生成（百度主/本地兜底）
- DELETE /api/ppt/{task_id}     删除任务
- GET  /ppt-files/{filename}    静态托管生成的 pptx（P07 导出下载）

注意：生成接口为同步阻塞（百度 SSE 全流程约 20-60s），
前端应展示"生成中"状态并禁用重复提交；任务表 status 记录 生成中/已完成/失败。
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.core.security import get_current_user
from src.schemas.ppt import (
    GenerateRequest,
    OutlineUpdate,
    PptCreate,
    PptTaskListOut,
    PptTaskOut,
)
from src.services.ppt_service import PptService
from src.services.ppt_template_service import list_templates, upload_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ppt", tags=["PPT 工坊"])


@router.get("/templates", summary="PPT 模板列表（内置 3 套 + 用户上传）")
def templates(user: dict = Depends(get_current_user)):
    return {"items": list_templates()}


@router.post("/templates", summary="上传自定义 PPT 模板（≤20MB）")
async def upload_tpl(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    info = upload_template(file, user["id"])
    return {"message": "模板上传成功", "template": info}


@router.post("", response_model=PptTaskOut, status_code=201, summary="P01 新建任务（AI 生成大纲）")
def create_task(
    data: PptCreate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return PptService.create_task(db, user["id"], data)


@router.get("", response_model=PptTaskListOut, summary="P03 我的 PPT 任务列表")
def list_tasks(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return PptService.list_tasks(db, user["id"], page, page_size)


@router.get("/{task_id}", response_model=PptTaskOut, summary="P03 任务详情")
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return PptService.get_task(db, user["id"], task_id)


@router.put("/{task_id}/outline", response_model=PptTaskOut, summary="P02 编辑大纲")
def update_outline(
    task_id: int,
    data: OutlineUpdate,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return PptService.update_outline(db, user["id"], task_id, data.outline)


@router.post("/{task_id}/generate", response_model=PptTaskOut, summary="P03 触发生成（百度主/本地兜底）")
def generate(
    task_id: int,
    data: GenerateRequest | None = None,
    use_local: bool = Query(default=False, description="强制本地生成（调试/演示）"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    data = data or GenerateRequest()
    return PptService.generate(
        db,
        user["id"],
        task_id,
        outline_override=data.outline,
        template_override=data.template,
        use_local=use_local,
    )


@router.delete("/{task_id}", summary="删除任务")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    PptService.delete_task(db, user["id"], task_id)
    return {"message": "任务已删除"}
