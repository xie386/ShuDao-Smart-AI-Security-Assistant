"""PPT 工坊路由（v2.0 大点 02 重写）。

端点一览：
- GET    /api/ppt/capabilities            当前可用链路（agent / 直连 LLM / OfficeCLI / 视觉 / 配图）
- GET    /api/ppt/templates               模板列表（内置兜底 + 共享模板库）
- POST   /api/ppt/templates               上传模板（≤20MB，python-pptx 校验）
- POST   /api/ppt                         P01 新建任务（LLM 出大纲，状态=草稿）
- GET    /api/ppt                         我的任务列表
- GET    /api/ppt/{id}                    任务详情
- PUT    /api/ppt/{id}/outline            P02 编辑大纲
- POST   /api/ppt/{id}/agent              **主力**：SSE 逐阶段生成（PPT Agent）
- GET    /api/ppt/{id}/progress           断线/刷新后恢复进度
- GET    /api/ppt/{id}/workspace          工作区清单（截图/图片/中间稿/日志/成品）
- DELETE /api/ppt/{id}/workspace          清理工作区（成品默认保留）
- GET    /api/ppt/{id}/assets/shots/{name} 逐页截图（仅本人；不下发 Unsplash 原图，避免二次托管）
- GET    /api/ppt/{id}/sources            图片来源（摄影师 / 授权 / 可点击链接）
- POST   /api/ppt/{id}/generate           快速模式：直接渲染大纲（无 Agent / 无配图）
- GET    /api/ppt/{id}/download           下载（仅本人）
- DELETE /api/ppt/{id}                    删除任务（含工作区）

SSE 帧（与 `src/ai/ppt_agent.py` 的事件同源）：
    data: {"stage":"S6","progress":62,"page":3,"page_total":10,"shot":"/api/ppt/12/assets/shots/page-03.png",...}
    data: {"done":true,"file":"/api/ppt/12/download","pages":10,...}
    data: [DONE]
"""
import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from src.ai import ppt_agent, ppt_llm
from src.core.db import SessionLocal, get_db
from src.core.paths import safe_join, task_dir
from src.core.security import get_current_user
from src.schemas.ppt import (
    GenerateRequest,
    OutlineUpdate,
    PptAgentSpec,
    PptCreate,
    PptTaskListOut,
    PptTaskOut,
)
from src.services import ppt_progress
from src.services.ppt_service import PptService
from src.services.ppt_template_service import list_templates, upload_template

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ppt", tags=["PPT 工坊"])


# --------------------------------------------------------------------------- #
# 静态路径（必须放在 /{task_id} 之前，否则会被当成 task_id）
# --------------------------------------------------------------------------- #
@router.get("/capabilities", summary="当前可用链路与能力")
def capabilities(user: dict = Depends(get_current_user)):
    return ppt_llm.capabilities()


@router.get("/templates", summary="PPT 模板列表（内置兜底 + 共享模板库）")
def templates(user: dict = Depends(get_current_user)):
    return {"items": list_templates()}


@router.post("/templates", summary="上传自定义 PPT 模板（≤20MB）")
async def upload_tpl(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    info = upload_template(file, user["id"])
    return {"message": "模板上传成功", "template": info}


# --------------------------------------------------------------------------- #
# 任务 CRUD
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Agent 生成（SSE）
# --------------------------------------------------------------------------- #
def _sse(obj) -> str:
    if obj == "[DONE]":
        return "data: [DONE]\n\n"
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def _public_event(task_id: int, ev: dict) -> dict:
    """把内部事件转成前端可用的事件（截图绝对路径 → 鉴权下载 URL）。"""
    out = dict(ev)
    shot = out.pop("shot", None)
    if shot:
        out["shot"] = f"/api/ppt/{task_id}/assets/shots/{str(shot).replace(chr(92), '/').split('/')[-1]}"
    if out.get("done"):
        out["file"] = f"/api/ppt/{task_id}/download"
        out.pop("file_path", None)
    return out


@router.post("/{task_id}/agent", summary="PPT 生成 Agent（SSE 流式进度）")
def generate_agent(
    task_id: int,
    spec: PptAgentSpec | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """逐阶段生成（S0–S8）。前端用 fetch 读流，展示进度条与工作日志。"""
    spec = spec or PptAgentSpec()
    user_id = user["id"]
    prepared = PptService.start_agent(db, user_id, task_id, spec)
    db.commit()   # 状态"生成中"先落库，前端立即能看到

    def stream():
        # 生成过程可能几分钟：用独立 session，避免请求级 session 在流式响应期间被关闭
        session = SessionLocal()
        try:
            for ev in ppt_agent.run(prepared, user_id=user_id, task_id=task_id):
                ppt_progress.set_progress(task_id, ev)
                yield _sse(_public_event(task_id, ev))
                if ev.get("done"):
                    PptService.finish_agent(session, task_id, ev)
            yield _sse("[DONE]")
        except Exception as exc:  # noqa: BLE001
            logger.exception("PPT Agent 生成失败 task=%s", task_id)
            msg = f"{type(exc).__name__}: {exc}"
            PptService.fail_agent(session, task_id, msg)
            ppt_progress.set_progress(task_id, {"stage": "S8", "progress": 100, "msg": msg}, status="failed")
            yield _sse({"error": msg, "stage": "S8"})
            yield _sse("[DONE]")
        finally:
            session.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{task_id}/progress", summary="读取生成进度（断线/刷新恢复）")
def progress(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    task = PptService.get_task(db, user["id"], task_id)   # 归属校验 + 卡死兜底
    data = ppt_progress.get_progress(task_id) or {}
    return {
        "task_id": task_id,
        "status": task.status,
        "stage": data.get("stage") or "",
        "progress": int(data.get("progress") or (100 if task.status == "已完成" else 0)),
        "page": int(data.get("page") or 0),
        "page_total": int(data.get("page_total") or 0),
        "msg": data.get("msg") or "",
        "updated": int(data.get("updated") or 0),
        "download": f"/api/ppt/{task_id}/download" if task.status == "已完成" else None,
    }


# --------------------------------------------------------------------------- #
# 工作区与资源
# --------------------------------------------------------------------------- #
@router.get("/{task_id}/workspace", summary="工作区清单（用户可见资产）")
def workspace(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    return PptService.workspace(db, user["id"], task_id)


@router.delete("/{task_id}/workspace", summary="清理工作区（成品默认保留）")
def cleanup_workspace(
    task_id: int,
    keep_out: bool = Query(default=True, description="true=保留成品目录 out/"),
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    result = PptService.cleanup_workspace(db, user["id"], task_id, keep_out=keep_out)
    return {"message": "工作区已清理", **result}


@router.get("/{task_id}/sources", summary="图片来源与署名（合规展示用）")
def sources(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """返回本次生成的图片署名清单（摄影师 / 授权 / 原图链接）。

    只给元数据，**不下发图片文件**：Unsplash 要求图片热链到其 CDN，
    我们不把原图托管成自家静态资源（原图仅服务端排版时使用）。
    """
    PptService.get_task(db, user["id"], task_id)          # 归属校验
    path = task_dir(user["id"], task_id, "images") / "sources.json"
    if not path.is_file():
        return {"items": []}
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    return {"items": items if isinstance(items, list) else []}


@router.get("/{task_id}/assets/{kind}/{name}", summary="逐页截图（仅本人）")
def assets(
    task_id: int,
    kind: str,
    name: str,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    # 只放行我们自己渲染的截图；Unsplash 原图不下发（合规：不二次托管图片）
    if kind != "shots":
        raise HTTPException(status_code=404, detail="资源类型不存在")
    PptService.get_task(db, user["id"], task_id)      # 归属校验（非本人 403/404）
    root = task_dir(user["id"], task_id, kind)
    try:
        path = safe_join(root, name)                  # 防 `..` 与绝对路径
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="资源名非法") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    media = "image/png" if path.suffix.lower() == ".png" else "application/octet-stream"
    return FileResponse(path, media_type=media)


# --------------------------------------------------------------------------- #
# 快速模式 / 下载 / 删除
# --------------------------------------------------------------------------- #
@router.post("/{task_id}/generate", response_model=PptTaskOut, summary="快速模式：直接渲染大纲")
def generate(
    task_id: int,
    data: GenerateRequest | None = None,
    use_local: bool = Query(default=True, description="保留参数（当前只有本地渲染一条实现）"),
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


@router.get("/{task_id}/download", summary="P07 下载 PPT（仅本人）")
def download(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    path, download_name = PptService.download_path(db, user["id"], task_id)
    return FileResponse(
        path,
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


@router.delete("/{task_id}", summary="删除任务")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    PptService.delete_task(db, user["id"], task_id)
    return {"message": "任务已删除"}
