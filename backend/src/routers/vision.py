"""图片隐患识别路由（v2.0 大点 06）。

接口：
- `POST /api/vision/hazard`        一次性 JSON（保留兼容；内部同样走 B 混合编排）
- `POST /api/vision/hazard/stream` SSE 分阶段（识别 → 检索知识 → 补充资料 → 汇总），裁定 11
- `POST /api/vision/annotate`      上报前烘焙标注图（前端渲染一份、服务端存一份，裁定 4）
- `GET  /api/vision/box-style`     框样式配置（前后端唯一事实源，裁定 1）

SSE 事件（每条 `data: {json}`，末尾 `data:[DONE]`）：
    {"stage":"cache"} → {"stage":"vision"} → {"stage":"vision_done",count}
    → {"stage":"rag",queries} → {"stage":"rag_done",scores,gaps,top}
    →（有缺口）{"stage":"websearch"} → {"stage":"websearch_done",ok,hosts,contrib_id}
    → {"stage":"synthesize"} → {"stage":"done","result":{...}}
失败：{"stage":"error","msg":"..."} 后 `[DONE]`，不抛 500。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.ai import hazard_agent
from src.ai.vision import vision_client
from src.core import box_style
from src.core.config import settings
from src.core.security import get_current_user
from src.services import hazard_analysis_service as svc
from src.utils.files import validate_and_save

router = APIRouter(prefix="/api/vision", tags=["图片识别"])
logger = logging.getLogger(__name__)

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


class AnnotateIn(BaseModel):
    image_url: str = Field(..., description="/uploads/xxx.jpg 形式的原图地址")
    hazards: list[dict] = Field(default_factory=list, description="含 location/level/category 的隐患列表")
    labels: str = Field("index", description="index=只显示序号；full=序号+等级+类别+名称")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _resolve_upload(image_url: str) -> Path:
    """/uploads/xxx.jpg → 磁盘路径；拒绝穿越与外部 URL。"""
    url = image_url or ""
    if not url.startswith("/uploads/"):
        raise HTTPException(status_code=400, detail="image_url 必须以 /uploads/ 开头")
    name = url.split("/uploads/", 1)[1]
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法图片路径")
    path = Path(settings.UPLOAD_DIR) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="图片不存在")
    return path


def _dims(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except Exception:  # noqa: BLE001
        return 0, 0


def _boxes_url_for(src: Path, hazards: list[dict]) -> str | None:
    """服务端烘焙标注图（JPG q90）；无坐标则返回 None。"""
    if not any(isinstance(h.get("location"), (list, tuple)) and len(h["location"]) == 4 for h in hazards):
        return None
    out = src.with_name(src.stem + "_boxes.jpg")
    drawn = svc.render_annotated(src, hazards, out)
    return f"/uploads/{out.name}" if drawn else None


# --------------------------------------------------------------------------- #
# 样式配置（前后端唯一事实源）
# --------------------------------------------------------------------------- #
@router.get("/box-style", summary="隐患框样式配置（色板/透明度/线宽规则）")
def box_style_config():
    return box_style.palette()


# --------------------------------------------------------------------------- #
# 一次性识别（兼容旧调用）
# --------------------------------------------------------------------------- #
@router.post(
    "/hazard",
    summary="图片隐患识别（视觉 + 知识库依据）",
    description="上传现场图片 + 可选现场描述，返回结构化隐患分析（类别/等级/建议/引用/坐标），"
    "并生成标注图 boxes_url。内部走「视觉 → 并发 RAG → 缺口网搜 → 汇总」编排。",
)
def analyze_hazard(
    file: UploadFile = File(..., description="jpg/jpeg/png/webp，≤10MB"),
    scene_description: str = Form(default="", description="现场描述（可选）"),
    user: dict = Depends(get_current_user),
):
    url = validate_and_save(file)
    file.file.seek(0)
    image_bytes = file.file.read()
    try:
        result = svc.analyze(image_bytes, scene_description)
    except RuntimeError as exc:  # key 未配置 / 模型不可用
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    src = Path(settings.UPLOAD_DIR) / Path(url).name
    hazards = result.get("hazards") or []
    boxes_url = _boxes_url_for(src, hazards)
    w, h = _dims(src)
    return {
        "image_url": url,
        "boxes_url": boxes_url,
        "image_width": w,
        "image_height": h,
        "analysis": result,
        "gap": result.get("gap"),
    }


# --------------------------------------------------------------------------- #
# SSE 分阶段识别（裁定 11）
# --------------------------------------------------------------------------- #
@router.post(
    "/hazard/stream",
    summary="图片隐患识别（SSE 分阶段：识别 → 检索 → 补充 → 汇总）",
    description="与 /hazard 同参，但以 SSE 推送阶段进度；总预算 ≤200s，任一阶段失败按阶段降级。",
)
async def analyze_hazard_stream(
    request: Request,
    file: UploadFile = File(...),
    scene_description: str = Form(default=""),
    user: dict = Depends(get_current_user),
):
    url = validate_and_save(file)
    file.file.seek(0)
    image_bytes = file.file.read()
    src = Path(settings.UPLOAD_DIR) / Path(url).name

    async def gen():
        t0 = time.perf_counter()

        def remain(limit: float) -> float:
            return max(0.0, limit - (time.perf_counter() - t0))

        key = svc.cache_key(image_bytes, scene_description)
        cached = svc._cache_get(key)
        if cached:
            yield _sse({"stage": "cache", "msg": "命中 10 分钟内缓存，直接返回上次结果"})
            yield _sse({"stage": "done", "result": cached})
            yield "data:[DONE]\n\n"
            return

        # 1) 视觉
        yield _sse({"stage": "vision", "msg": "正在识别现场隐患…"})
        try:
            res = await run_in_threadpool(vision_client.analyze_hazard, image_bytes, scene_description)
        except Exception as exc:  # noqa: BLE001 —— 识别失败给结构化错误，不抛 500
            logger.warning("SSE 识别失败：%s", exc)
            yield _sse({"stage": "error", "msg": f"识别失败：{exc}"[:200]})
            yield "data:[DONE]\n\n"
            return
        if await request.is_disconnected():
            return

        scale = res.pop("_scale", None)
        hazards = svc.to_original(res.get("hazards") or [], scale)
        res["hazards"] = hazards
        yield _sse({"stage": "vision_done", "detected": bool(res.get("detected")),
                    "count": len(hazards), "summary": (res.get("summary") or "")[:150]})

        # 2) 检索知识库
        if hazards and settings.HAZARD_AGENT_ENABLED:
            queries = [hazard_agent._query_of(h) for h in hazards]
            yield _sse({"stage": "rag", "msg": "检索知识库…", "queries": queries})
            try:
                rag = await run_in_threadpool(hazard_agent.retrieve_all, hazards)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE 检索失败：%s", exc)
                rag = {"kb": {}, "scores": {}, "gaps": list(range(1, len(hazards) + 1)),
                       "queries": queries, "secs": 0.0}
            top = [
                {
                    "index": i,
                    "score": round(rag["scores"].get(i, 0.0), 3),
                    "doc": (rag["kb"].get(i) or [{}])[0].get("doc", ""),
                    "chapter": (rag["kb"].get(i) or [{}])[0].get("chapter", ""),
                }
                for i in range(1, len(hazards) + 1)
            ]
            yield _sse({"stage": "rag_done", "top": top, "gaps": rag["gaps"], "secs": rag["secs"]})
            if await request.is_disconnected():
                return

            # 3) 缺口补料（合并一次）
            web = {"ok": False, "md": "", "hosts": [], "note": "", "secs": 0.0}
            if rag["gaps"]:
                yield _sse({"stage": "websearch", "msg": f"知识库缺 {len(rag['gaps'])} 条依据，正在联网补充…",
                            "indices": rag["gaps"]})
                try:
                    web = await run_in_threadpool(hazard_agent.fill_gap, hazards, rag["gaps"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("SSE 网搜失败：%s", exc)
                    web = {"ok": False, "md": "", "hosts": [], "note": f"{type(exc).__name__}", "secs": 0.0}
                yield _sse({"stage": "websearch_done", "ok": bool(web.get("ok")),
                            "hosts": web.get("hosts") or [], "contrib_id": web.get("contrib_id"),
                            "note": web.get("note") or "", "secs": web.get("secs")})
                if await request.is_disconnected():
                    return

            # 4) 汇总建议
            yield _sse({"stage": "synthesize", "msg": "按依据汇总整改建议…"})
            try:
                syn = await run_in_threadpool(hazard_agent.synthesize, hazards, rag["kb"], web)
                res["hazards"] = syn["hazards"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE 汇总失败：%s", exc)
                for h in hazards:
                    h.update({"suggestion": "", "refs": [], "grounded": False,
                              "gap_note": "建议生成失败"})
                res["hazards"] = hazards
            res["gap"] = {"indices": rag["gaps"], "web": {k: v for k, v in web.items() if k != "md"}}
            res["elapsed_s"] = round(time.perf_counter() - t0, 1)
            yield _sse({"stage": "synthesize_done", "generated": sum(1 for h in res["hazards"] if h.get("grounded"))})
        elif not hazards:
            res["gap"] = None

        # 5) 烘焙标注图 + 收尾
        if await request.is_disconnected():
            return
        boxes_url = await run_in_threadpool(_boxes_url_for, src, res["hazards"])
        w, h = _dims(src)
        res.update({"image_url": url, "boxes_url": boxes_url, "image_width": w, "image_height": h,
                    "cached": False, "elapsed_s": round(time.perf_counter() - t0, 1)})
        svc._cache_set(key, res)
        yield _sse({"stage": "done", "result": res})
        yield "data:[DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# --------------------------------------------------------------------------- #
# 上报前烘焙标注图（裁定 4：前端渲一份给用户看，服务端存一份）
# --------------------------------------------------------------------------- #
@router.post("/annotate", summary="把隐患框烘焙进图片（JPG），返回标注图地址")
def annotate(body: AnnotateIn, user: dict = Depends(get_current_user)):
    src = _resolve_upload(body.image_url)
    hazards = body.hazards or []
    labels = "full" if body.labels == "full" else "index"
    out = src.with_name(f"{src.stem}_boxes.jpg")
    try:
        drawn = svc.render_annotated(src, hazards, out, labels=labels)
    except Exception as exc:  # noqa: BLE001
        logger.warning("标注图烘焙失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"标注图生成失败：{exc}") from exc
    if not drawn:
        raise HTTPException(status_code=400, detail="隐患列表中没有可用的坐标")
    return {"annotated_url": f"/uploads/{out.name}", "count": drawn}
